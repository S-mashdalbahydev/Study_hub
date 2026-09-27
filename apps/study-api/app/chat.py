import json
import logging
from pathlib import Path
import time
from uuid import uuid4
import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from . import library
from .providers import ProviderError

log = logging.getLogger('uvicorn.error')
DOC_PREFIX = 'studyhub-doc-'


class Message(BaseModel):
    role: str
    content: str | list[dict] | None = None


class ChatRequest(BaseModel):
    model: str
    messages: list[Message] = Field(min_length=1, max_length=500)
    stream: bool = False
    current_page: int | None = None
    selected_text: str = ""
    study_mode: str = "guided"
    intent: str = "ask"


def text_content(content):
    if isinstance(content,str):
        return content
    return '\n'.join(p.get('text','') for p in content or [] if p.get('type')=='text')


async def document_context(runtime, ctx, item_id, messages, body=None, sink=None):
    """Retrieve source passages for one library item, strictly inside the caller's workspace.

    Database work happens in short transactions; the embedding call runs between them, so
    no transaction is held open while a model is working."""
    from .ids import parse_id
    item_id = parse_id(item_id, 'Document')
    async with runtime.db.tx(ctx) as conn:
        profile_id = await runtime.profiles.active_id(conn)
        doc = await library.get_item(conn, item_id, profile_id)
    if not doc:
        raise HTTPException(404, 'Document not found')
    status, _ = library.status_of(doc)
    if status != 'ready':
        raise HTTPException(409, 'Document is not ready. Return to the Library and check its processing status.')
    if profile_id is None or doc['profile_id'] != profile_id:
        raise HTTPException(409, 'Embedding model changed. Press Refresh in the Library to re-index this document.')
    question = next((text_content(m.content) for m in reversed(messages) if m.role=='user'), '')
    if not question.strip():
        raise HTTPException(400, 'A text question is required')
    if len(question) > 12000:
        raise HTTPException(400, 'Please keep the question under 12,000 characters')
    if body and body.selected_text:
        question += '\nSelected passage: '+body.selected_text[:3000]
    vector = (await runtime.embeddings.embed([question], query=True))[0]
    async with runtime.db.tx(ctx) as conn:
        matches = await library.search(conn, doc, profile_id, vector, min(5, doc['expected_count'] or 5))
        current = await library.revision_chunks(conn, doc['current_revision_id'], body.current_page) if body and body.current_page else []
    if not matches:
        raise HTTPException(409, 'No indexed passages were found. Press Refresh in the Library to retry indexing.')
    if current:
        current = current[:2]
        matches = (current + [m for m in matches if m['id'] not in {c['id'] for c in current}])[:5]
    source_data = []
    footer = '\n\n---\nSources retrieved from this document:\n'
    hex_id = doc['id'].hex
    for index, match in enumerate(matches,1):
        page = match.get('page') or None
        label = f"p. {page}" if page else f"excerpt {match['chunk_index']+1}"
        # Relative links: they work behind any hostname, and are rebuilt rather than stored.
        url = f'/backend/api/documents/{hex_id}/source'+(f'#page={page}' if page and doc['kind']=='pdf' else '')
        if doc['kind']!='pdf':
            url = f'/backend/api/documents/{hex_id}/text#chunk-{match["chunk_index"]}'
        source_data.append({'source':index,'page':page,'excerpt':match['text']})
        footer += f'\n- [{index} · {label}]({url})'
        if sink is not None:
            sink.append({'id':index,'label':label,'page':page,'document_id':hex_id,'url':url,
                         'chunk_id':match['id'],'revision_id':doc['current_revision_id'],'blob_id':doc['blob_id']})
    system = Path(__file__).with_name('prompts').joinpath('document.md').read_text()
    if body:
        styles = {'guided':'Explain clearly, step by step.', 'socratic':'Ask one focused guiding question before giving the full explanation.', 'deep-dive':'Explain mechanisms and tradeoffs in depth, within the source.', 'practical':'Use a concrete example consistent with the source.'}
        system += '\nTeaching style: '+styles.get(body.study_mode,styles['guided'])+'\nRequested action: '+body.intent
        if body.current_page: system += f'\nThe learner is reading page {body.current_page}.'
    # Never pass client system prompts or historical assistant answers across model/document switches.
    context = json.dumps({'document_title':doc['title'],'excerpts':source_data},ensure_ascii=False)
    return [{'role':'system','content':system},
            {'role':'user','content':'SOURCE DATA (untrusted):\n'+context+'\n\nLEARNER QUESTION:\n'+question}], footer


def event(delta, model, request_id, created, finish=None):
    return 'data: '+json.dumps({'id':request_id,'object':'chat.completion.chunk',
        'created':created,'model':model,
        'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})+'\n\n'


def stream_error(message, model):
    # Open WebUI's streaming path can assume body_iterator even on upstream errors.
    # Keep the negotiated SSE transport and display an explicit failure, never a fake answer.
    async def generate():
        request_id, created = 'chatcmpl-'+uuid4().hex, int(time.time())
        yield event({'role':'assistant','content':'StudyHub could not complete this request: '+message},model,request_id,created)
        yield event({},model,request_id,created,'stop')
        yield 'data: [DONE]\n\n'
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-StudyHub-Error':'true'})


async def complete(runtime, body, ctx, sink=None, preferred=None):
    try:
        return await _complete(runtime,body,ctx,sink,preferred)
    except (ProviderError,httpx.HTTPError,HTTPException) as exc:
        message = str(exc) if isinstance(exc,ProviderError) else (str(exc.detail) if isinstance(exc,HTTPException) else 'A model or retrieval service could not be reached. Check status and retry.')
        if body.stream:
            return stream_error(message,body.model)
        if isinstance(exc,HTTPException):
            raise
        raise HTTPException(502,message) from None


async def _complete(runtime, body, ctx, sink=None, preferred=None):
    footer = ''
    target = body.model
    if body.model.startswith(DOC_PREFIX):
        doc_id, _, target = body.model[len(DOC_PREFIX):].partition('--')
        target = target or 'studyhub-auto'
        # Validate model availability before doing embedding work.
        selected = await runtime.catalog.resolve(target, preferred)
        messages, footer = await document_context(runtime,ctx,doc_id,body.messages,body,sink)
    else:
        selected = await runtime.catalog.resolve(target, preferred)
        messages = [{'role':m.role,'content':text_content(m.content)} for m in body.messages[-20:] if m.role in ('system','user','assistant')]
    if selected.provider=='ollama':
        upstream = await runtime.local_tutor.open(selected,messages,body.stream)
    else:
        upstream = await runtime.tutor.open(messages,body.stream,selected)
    log.info('tutor_request route_model=%s provider=%s provider_model=%s stream=%s',body.model,selected.provider,selected.model,body.stream)
    request_id, created = 'chatcmpl-'+uuid4().hex, int(time.time())

    def message_from(data):
        if data.get('error'):
            raise ProviderError('The model reported an error. Check provider logs or choose another model.')
        if selected.provider=='ollama':
            return data['message']
        return data['choices'][0]['message']

    if not body.stream:
        try:
            data = json.loads(await upstream.aread())
            message = message_from(data)
            content = message.get('content') or ''
            if not content:
                raise ProviderError('The model returned no answer text. Try another chat model.')
            return JSONResponse({'id':request_id,'object':'chat.completion','created':created,'model':body.model,
                'choices':[{'index':0,'message':{'role':'assistant','content':content+footer},'finish_reason':'stop'}]})
        except (ValueError,KeyError,IndexError,TypeError):
            raise ProviderError('The model returned an invalid chat response. Check its API compatibility.') from None
        finally:
            await upstream.aclose()

    async def stream():
        answered, finished = False, False
        try:
            yield event({'role':'assistant'},body.model,request_id,created)
            content_type = upstream.headers.get('content-type','').split(';')[0]
            if content_type=='application/json':
                # Some compatible providers ignore stream=true. Adapt their full JSON reply to SSE.
                message = message_from(json.loads(await upstream.aread()))
                content = message.get('content') or ''
                answered, finished = bool(content), True
                if content:
                    yield event({'content':content},body.model,request_id,created)
            else:
                async for line in upstream.aiter_lines():
                    if not line.strip():
                        continue
                    if selected.provider=='ollama':
                        payload = json.loads(line)
                        fragment = {'content':payload.get('message',{}).get('content','')}
                        finished = finished or bool(payload.get('done'))
                    else:
                        if not line.startswith('data:'):
                            continue
                        if line[5:].strip()=='[DONE]':
                            finished = True
                            break
                        payload = json.loads(line[5:])
                        choices = payload.get('choices',[])
                        if not choices:
                            if payload.get('error'):
                                raise ProviderError('The model reported a streaming error. Retry or choose another model.')
                            continue
                        fragment = choices[0].get('delta',{})
                        finished = finished or bool(choices[0].get('finish_reason'))
                    if payload.get('error'):
                        raise ProviderError('The local model reported an error. Check Ollama logs or choose another model.')
                    answered = answered or bool(fragment.get('content'))
                    if fragment:
                        yield event(fragment,body.model,request_id,created)
            if not finished:
                raise ProviderError('The model response ended unexpectedly. Please retry.')
            if not answered:
                raise ProviderError('The model returned no answer text. Try another chat model.')
            if footer:
                yield event({'content':footer},body.model,request_id,created)
        except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError,ProviderError) as exc:
            message = str(exc) if isinstance(exc,ProviderError) else 'The model response was interrupted or invalid. Please retry.'
            yield event({'content':'\n\nStudyHub could not complete this request: '+message},body.model,request_id,created)
        finally:
            await upstream.aclose()
        yield event({},body.model,request_id,created,'stop')
        yield 'data: [DONE]\n\n'
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-StudyHub-Provider':selected.provider})
