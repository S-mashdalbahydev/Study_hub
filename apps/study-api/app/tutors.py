"""Tutor connections a workspace owns, and each user's preferred tutor.

Models can be pulled into the shared local Ollama (administrator only), and any
OpenAI-compatible endpoint can be added, tested and chosen from the UI. API keys are
encrypted at rest and never sent back to the browser; the API only reports whether one exists.
"""
import ipaddress
import json
import socket
from time import perf_counter
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import require_admin, require_user
from .catalog import TutorChoice
from .db import one, rows
from .ids import parse_id, public
from .secrets import SecretsUnavailable, decrypt, encrypt

router=APIRouter(prefix='/api/tutors')

# Sizes are the download, not the memory needed to run comfortably.
RECOMMENDED=[
 {'name':'gemma3:4b','size_gb':3.3,'good_for':'Recommended. Writes lessons straight out, so every second of a small GPU goes into the answer.'},
 {'name':'llama3.1:8b','size_gb':4.9,'good_for':'Wider knowledge and steadier writing. Wants 8 GB of memory; slow on a 4 GB card.'},
 {'name':'qwen3:4b','size_gb':2.5,'good_for':'Good at planning, but thinks out loud before answering, which is slow on a small GPU.'},
 {'name':'qwen3:8b','size_gb':5.2,'good_for':'Deeper answers than the 4b, with the same thinking-out-loud cost.'},
 {'name':'phi4-mini:3.8b','size_gb':2.5,'good_for':'Fast and light, strong reasoning for its size.'},
 {'name':'qwen3.5:0.8b','size_gb':1.0,'good_for':'Tiny and quick. Too weak to write lessons; fine for smoke tests.'},
]


async def choice_for(runtime, ctx):
    """Providers this workspace may use (system-wide plus its own) and the user's pick."""
    async with runtime.db.tx(ctx) as conn:
        found=await rows(conn,'''SELECT id,scope,label,kind,base_url,model,secret_ciphertext,secret_nonce,secret_key_version
                                  FROM tutor_providers ORDER BY created_at''')
        default=await one(conn,'SELECT default_tutor_id FROM user_settings WHERE user_id=%s',(ctx.user_id,))
    providers=[]
    for row in found:
        try:
            key=decrypt(runtime.settings,row['secret_ciphertext'],row['secret_nonce'],row['secret_key_version'],row['id'])
        except SecretsUnavailable:
            key=''
        providers.append({'id':row['id'].hex,'scope':row['scope'],'label':row['label'],'kind':row['kind'],
                          'base_url':row['base_url'],'model':row['model'],'api_key':key,
                          'key_present':row['secret_ciphertext'] is not None})
    wanted=default['default_tutor_id'].hex if default and default['default_tutor_id'] else None
    return TutorChoice(tuple(providers),next((p for p in providers if p['id']==wanted),None))


def listing(choice):
    chosen=choice.preferred['id'] if choice.preferred else None
    return [{'id':p['id'],'label':p['label'],'kind':p['kind'],'base_url':p['base_url'],'model':p['model'],
             'key_present':p['key_present'],'active':p['id']==chosen,'scope':p['scope']} for p in choice.providers]


def check_egress(settings, url):
    """Hosted installs must not let users point the server at its own private network."""
    if settings.allow_private_provider_urls:
        return
    host=urlparse(url).hostname or ''
    trusted={urlparse(u.strip()).hostname for u in (settings.ollama_chat_urls or settings.ollama).split(';') if u.strip()}
    if host in trusted:
        return
    try:
        addresses={info[4][0] for info in socket.getaddrinfo(host,None)}
    except socket.gaierror:
        raise HTTPException(422,'That address could not be resolved.') from None
    if any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise HTTPException(422,'Private and local network addresses are not allowed on this StudyHub.')


class Connection(BaseModel):
    label:str=Field(default='',max_length=80)
    kind:str=Field(pattern='^(ollama|openai)$')
    base_url:str=Field(min_length=4,max_length=400)
    api_key:str=Field(default='',max_length=400)
    model:str=Field(default='',max_length=200)

    def normalised(self):
        url=self.base_url.strip().rstrip('/')
        parsed=urlparse(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname:
            raise HTTPException(422,'Enter a full address starting with http:// or https://')
        return url


async def probe(client,body:Connection,url):
    """Ask the endpoint what it can do. Never sends a completion request."""
    started=perf_counter()
    headers={'Authorization':'Bearer '+body.api_key} if body.api_key else {}
    target=url+('/api/tags' if body.kind=='ollama' else '/models')
    response=await client.get(target,headers=headers,timeout=15,follow_redirects=False)
    elapsed=round((perf_counter()-started)*1000,1)
    if response.is_error:
        raise HTTPException(502,f'The endpoint answered HTTP {response.status_code}. Check the address, the key and the path.')
    payload=response.json()
    if body.kind=='ollama':
        names=[m.get('name') or m.get('model') for m in payload.get('models',[])]
    else:
        names=[m.get('id') for m in payload.get('data',payload if isinstance(payload,list) else [])]
    return {'ok':True,'latency_ms':elapsed,'models':[n for n in names if n][:100]}


async def state(runtime, ctx, force=False):
    choice=await choice_for(runtime,ctx)
    return {'providers':listing(choice),'catalog':await runtime.catalog.status(force,choice)}


@router.get('')
async def index(request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime
    return {**await state(runtime,principal.ctx),'recommended':RECOMMENDED,
            'ollama_url':runtime.settings.ollama,'can_manage_models':principal.is_admin}


@router.post('/test')
async def test(body:Connection,request:Request):
    await require_user(request)
    url=body.normalised();check_egress(request.app.state.runtime.settings,url)
    try:
        return await probe(request.app.state.client,body,url)
    except httpx.HTTPError as exc:
        raise HTTPException(502,f'Could not reach that address ({type(exc).__name__}). Check the URL and that the service is running.') from None
    except ValueError:
        raise HTTPException(502,'The endpoint replied with something that is not JSON. Check the base address.') from None


@router.post('',status_code=201)
async def save(body:Connection,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime;url=body.normalised()
    result=await test(body,request)
    if body.kind=='openai' and not body.model:
        raise HTTPException(422,'Choose which model this connection should use.')
    async with runtime.db.tx(principal.ctx) as conn:
        row=await one(conn,'''INSERT INTO tutor_providers (scope,workspace_id,label,kind,base_url,model,created_by)
                                VALUES ('workspace',%s,%s,%s,%s,%s,%s) RETURNING id''',
                      (principal.workspace_id,body.label.strip() or urlparse(url).hostname or body.kind,body.kind,url,
                       body.model.strip(),principal.user_id))
        if body.api_key.strip():
            try:
                cipher,nonce,version=encrypt(runtime.settings,body.api_key.strip(),row['id'])
            except SecretsUnavailable as exc:
                raise HTTPException(503,str(exc)) from None
            await conn.execute('''UPDATE tutor_providers SET secret_ciphertext=%s,secret_nonce=%s,secret_key_version=%s
                                  WHERE id=%s''',(cipher,nonce,version,row['id']))
    choice=await choice_for(runtime,principal.ctx)
    await runtime.catalog.discover(True,choice)
    return {'provider':next(p for p in listing(choice) if p['id']==row['id'].hex),'test':result}


async def set_default(conn, user_id, provider_id):
    await conn.execute('''INSERT INTO user_settings (user_id,default_tutor_id) VALUES (%s,%s)
                          ON CONFLICT (user_id) DO UPDATE SET default_tutor_id=EXCLUDED.default_tutor_id''',(user_id,provider_id))


@router.post('/{pid}/activate')
async def activate(pid:str,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime;provider=parse_id(pid,'Tutor connection')
    async with runtime.db.tx(principal.ctx) as conn:
        # RLS limits this lookup to system rows and this workspace's own connections.
        if not await one(conn,'SELECT 1 FROM tutor_providers WHERE id=%s',(provider,)):
            raise HTTPException(404,'That tutor connection no longer exists')
        await set_default(conn,principal.user_id,provider)
    return await state(runtime,principal.ctx,True)


@router.delete('/{pid}')
async def remove(pid:str,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime;provider=parse_id(pid,'Tutor connection')
    async with runtime.db.tx(principal.ctx) as conn:
        if not await one(conn,"DELETE FROM tutor_providers WHERE id=%s AND scope='workspace' RETURNING id",(provider,)):
            raise HTTPException(404,'That tutor connection no longer exists, or it is managed by the administrator')
    return {'providers':(await state(runtime,principal.ctx,True))['providers']}


@router.post('/models/{name:path}/activate')
async def activate_model(name:str,request:Request):
    """Make a locally installed model your tutor, without asking for any connection details."""
    principal=await require_user(request);runtime=request.app.state.runtime;url=runtime.settings.ollama
    installed=[m for m in await runtime.catalog.discover(force=True)
               if m.provider=='ollama' and m.model in (name,name+':latest')]
    if not installed:
        raise HTTPException(404,'That model is not installed. Ask the administrator to download it.')
    async with runtime.db.tx(principal.ctx) as conn:
        row=await one(conn,"SELECT id FROM tutor_providers WHERE kind='ollama' AND base_url=%s AND model=%s ORDER BY scope='system' DESC LIMIT 1",
                      (url,installed[0].model))
        if not row:
            row=await one(conn,'''INSERT INTO tutor_providers (scope,workspace_id,label,kind,base_url,model,created_by)
                                    VALUES ('workspace',%s,%s,'ollama',%s,%s,%s) RETURNING id''',
                          (principal.workspace_id,'Local · '+installed[0].model,url,installed[0].model,principal.user_id))
        await set_default(conn,principal.user_id,row['id'])
    return await state(runtime,principal.ctx,True)


class Pull(BaseModel):
    name:str=Field(min_length=1,max_length=120,pattern=r'^[A-Za-z0-9._:\-/]+$')


@router.post('/models/pull')
async def pull(body:Pull,request:Request):
    """Stream Ollama's download progress so the UI can show a real percentage."""
    await require_admin(request)
    runtime=request.app.state.runtime
    async def stream():
        sent_error=False
        try:
            request_body={'model':body.name,'stream':True}
            async with runtime.client.stream('POST',runtime.settings.ollama+'/api/pull',
                                             json=request_body,timeout=None) as response:
                if response.is_error:
                    await response.aread()
                    yield 'data: '+json.dumps({'error':f'Ollama returned HTTP {response.status_code}.'})+'\n\n'
                    return
                async for line in response.aiter_lines():
                    if not line.strip():continue
                    try:payload=json.loads(line)
                    except ValueError:continue
                    if payload.get('error'):
                        sent_error=True
                        yield 'data: '+json.dumps({'error':str(payload['error'])})+'\n\n'
                        break
                    total=payload.get('total') or 0
                    yield 'data: '+json.dumps({'status':payload.get('status',''),
                        'completed':payload.get('completed') or 0,'total':total,
                        'percent':round((payload.get('completed') or 0)/total*100) if total else None})+'\n\n'
        except httpx.HTTPError as exc:
            if not sent_error:
                yield 'data: '+json.dumps({'error':f'The download was interrupted ({type(exc).__name__}).'})+'\n\n'
        else:
            if not sent_error:
                await runtime.catalog.discover(force=True)
                yield 'data: '+json.dumps({'done':True})+'\n\n'
    return StreamingResponse(stream(),media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@router.delete('/models/{name:path}')
async def delete_model(name:str,request:Request):
    principal=await require_admin(request);runtime=request.app.state.runtime
    response=await runtime.client.request('DELETE',runtime.settings.ollama+'/api/delete',
                                          json={'model':name},timeout=60)
    if response.is_error:
        raise HTTPException(502,f'Ollama could not remove that model (HTTP {response.status_code}).')
    await runtime.catalog.discover(force=True)
    return {'catalog':await runtime.catalog.status(True,await choice_for(runtime,principal.ctx))}
