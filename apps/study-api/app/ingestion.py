"""Turning an original file into parsed text and retrieval chunks. No database access here."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
from .providers import ProviderError, checked

log = logging.getLogger('uvicorn.error')
SUPPORTED = {'.pdf','.md','.txt','.docx'}


class UnreadableDocument(ProviderError):
    """The file itself cannot be processed; retrying the same bytes will not help."""

MAX_BYTES = 128 * 1024 * 1024

def parse_markdown(text):
    """Preserve Markdown syntax while splitting at headings for useful retrieval."""
    sections=[];current=[];heading=None;level=None
    for line in text.splitlines():
        match=re.match(r'^(#{1,6})\s+(.+?)\s*$',line)
        if match and current:
            sections.append({'text':'\n'.join(current).strip(),'label':'section_header' if heading else None,
                             'heading':heading,'level':level})
            current=[]
        if match: heading=match[2].strip();level=len(match[1])
        current.append(line)
    if current:
        sections.append({'text':'\n'.join(current).strip(),'label':'section_header' if heading else None,
                         'heading':heading,'level':level})
    return {'texts':[section for section in sections if section['text']]}


def shallow_chunks(document, limit=1400):
    """Use Docling's text items and provenance; retain raw JSON for later structure work."""
    blocks = []
    for item in document.get('texts', []):
        text = item.get('text', '').strip()
        if not text:
            continue
        prov = item.get('prov') or []
        page = prov[0].get('page_no') if prov else None
        # Split only oversized text items. This is deliberately shallow, not chapter navigation.
        for start in range(0, len(text), limit):
            blocks.append({'text':text[start:start+limit], 'page':page})
    chunks = []
    for block in blocks:
        if chunks and chunks[-1]['page'] == block['page'] and len(chunks[-1]['text'])+len(block['text'])+2 <= limit:
            chunks[-1]['text'] += '\n\n'+block['text']
        else:
            chunks.append(dict(block))
    if not chunks:
        raise UnreadableDocument('No readable text was extracted. Try a text PDF or check Docling OCR support for this file.')
    return chunks


PARSER_VERSION = {'docling':'docling-serve-1.32/json-v1','markdown':'md-headings-v1','text':'paragraphs-v1'}
MIME = {'pdf':'application/pdf','md':'text/markdown','txt':'text/plain',
        'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def parser_for(kind):
    return 'markdown' if kind=='md' else ('text' if kind=='txt' else 'docling')


def processing_config(kind, chunk_chars):
    """What determines parser output. A change here produces a new processing revision."""
    parser=parser_for(kind)
    config={'parser':parser,'parser_version':PARSER_VERSION[parser],'chunk_chars':chunk_chars}
    fingerprint=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:24]
    return parser,PARSER_VERSION[parser],config,fingerprint


async def parse_document(client, settings, kind, path, filename):
    if kind in ('txt','md'):
        text=await asyncio.to_thread(Path(path).read_text,encoding='utf-8')
        if kind=='md':
            return parse_markdown(text)
        return {'texts':[{'text':p} for p in re.split(r'\n\s*\n',text) if p.strip()]}
    with open(path,'rb') as source:
        response=await client.post(settings.docling+'/v1/convert/file',
            files={'files':(filename,source)},
            data={'to_formats':'json','do_ocr':'true','image_export_mode':'placeholder','document_timeout':'3600'},
            timeout=3660)
    result=await checked(response,'Docling')
    if result.get('status')!='success':
        raise UnreadableDocument('Docling did not fully convert this file. Check Docling logs; Refresh retries the document.')
    parsed=result['document']['json_content']
    return json.loads(parsed) if isinstance(parsed,str) else parsed


def document_title(parsed, fallback):
    title=next((t.get('heading') or t.get('text','').strip() for t in parsed.get('texts',[])
                if t.get('label') in ('title','section_header')),fallback)
    return (title or fallback)[:200] or fallback
