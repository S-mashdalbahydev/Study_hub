"""Reading sessions and the structured chat stream for the product UI."""
import asyncio
import json
import re
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import library
from .auth import require_user
from .book import markdown_book
from .chat import ChatRequest, DOC_PREFIX, Message, complete
from .db import one, rows
from .ids import parse_id
from .structure import markdown_anchor, markdown_outline, source_structure  # noqa: F401  (re-exported for callers)
from .tutors import choice_for

router = APIRouter(prefix='/api')
UI_DEFAULTS = {'layout': 'split', 'zoom': 1.0, 'sidebar': True, 'chat_width': 42}
UI_FIELDS = set(UI_DEFAULTS)
# A reply still "generating" after this long belongs to a process that died.
STALE_REPLY = '15 minutes'


def session_out(row):
    ui = {**UI_DEFAULTS, **(row.get('ui_state') or {})}
    ui['sidebar'] = bool(ui['sidebar'])
    return {'id': row['id'].hex, 'document_id': row['library_item_id'].hex, 'page': row['page'], 'model': row['model'],
            'mode': row['mode'], **{k: ui[k] for k in UI_FIELDS},
            'created_at': row['created_at'].isoformat(), 'updated_at': row['updated_at'].isoformat()}


async def session_row(conn, sid):
    row = await one(conn, 'SELECT * FROM study_sessions WHERE id = %s', (sid,))
    if not row:
        raise HTTPException(404, 'Study session not found')
    return row


def citation_url(document_id, page):
    return f'/backend/api/documents/{document_id}/source'+(f'#page={page}' if page else '')


@router.get('/documents/{doc_id}/reader')
async def reader(doc_id: str, request: Request):
    principal = await require_user(request)
    runtime = request.app.state.runtime
    async with runtime.db.tx(principal.ctx) as conn:
        item = await library.require_item(conn, parse_id(doc_id, 'Document'))
        chunks = await library.revision_chunks(conn, item['current_revision_id']) if item['kind'] != 'pdf' else []
    info = await asyncio.to_thread(library.structure, runtime.storage, item)
    content = None
    has_cover = bool(library.cover_key(runtime.storage, item))
    if item['kind'] == 'md':
        content = await asyncio.to_thread(runtime.storage.read_text, item['object_key'], 'utf-8', 'replace')
        info = {**info, 'outline': markdown_outline(content), 'cover': has_cover}
    else:
        info = {**info, 'cover': has_cover}
    book = markdown_book(content, item['title'], item['filename'], has_cover) if content else None
    return {**info, 'content': content, 'book': book,
            'chunks': [{'text': c['text'], 'chunk_index': c['chunk_index'], 'page': c['page']} for c in chunks]}


@router.get('/documents/{doc_id}/preview')
async def preview(doc_id: str, request: Request):
    principal = await require_user(request)
    runtime = request.app.state.runtime
    async with runtime.db.tx(principal.ctx) as conn:
        item = await library.require_item(conn, parse_id(doc_id, 'Document'))
    key = library.cover_key(runtime.storage, item)
    if not key:
        raise HTTPException(404, 'Preview is not available yet')
    with runtime.storage.local_path(key) as path:
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})


class NewSession(BaseModel):
    document_id: str
    new: bool = False


@router.post('/sessions')
async def new_session(body: NewSession, request: Request):
    principal = await require_user(request)
    runtime = request.app.state.runtime
    item_id = parse_id(body.document_id, 'Document')
    async with runtime.db.tx(principal.ctx) as conn:
        # The stored original is enough for reading. AI preparation is asynchronous and must
        # never stand between a reader and their book.
        await library.require_item(conn, item_id)
        if not body.new:
            row = await one(conn, '''SELECT * FROM study_sessions WHERE library_item_id = %s AND created_by = %s
                                     ORDER BY updated_at DESC LIMIT 1''', (item_id, principal.user_id))
            if row:
                return session_out(row)
        row = await one(conn, '''INSERT INTO study_sessions (workspace_id, created_by, library_item_id)
                                 VALUES (%s, %s, %s) RETURNING *''', (principal.workspace_id, principal.user_id, item_id))
    return session_out(row)


@router.get('/sessions')
async def sessions(request: Request):
    principal = await require_user(request)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        found = await rows(conn, '''SELECT s.* FROM study_sessions s
                                    JOIN library_items li ON li.id = s.library_item_id AND li.state = 'active'
                                    WHERE s.created_by = %s ORDER BY s.updated_at DESC''', (principal.user_id,))
    return [session_out(r) for r in found]


@router.get('/sessions/{sid}')
async def get_session(sid: str, request: Request):
    principal = await require_user(request)
    session_id = parse_id(sid, 'Study session')
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        result = session_out(await session_row(conn, session_id))
        await conn.execute(f'''UPDATE messages SET status = 'cancelled' WHERE session_id = %s AND status = 'generating'
                               AND created_at < now() - interval '{STALE_REPLY}' ''', (session_id,))
        messages = await rows(conn, '''
            SELECT m.id, m.role, m.content, m.status, m.created_at,
                   COALESCE(json_agg(json_build_object('id', c.ordinal, 'label', c.label_snapshot, 'page', c.page,
                                                       'document_id', replace(c.library_item_id::text, '-', ''))
                                     ORDER BY c.ordinal) FILTER (WHERE c.message_id IS NOT NULL), '[]') AS citations
            FROM messages m LEFT JOIN citations c ON c.message_id = m.id
            WHERE m.session_id = %s GROUP BY m.id ORDER BY min(m.seq)''', (session_id,))
    result['messages'] = [{'id': m['id'].hex, 'role': m['role'], 'content': m['content'], 'status': m['status'],
                           'created_at': m['created_at'].isoformat(),
                           'citations': [{**c, 'url': citation_url(c['document_id'], c['page'])} for c in m['citations']]}
                          for m in messages]
    return result


class Position(BaseModel):
    page: int | None = Field(default=None, ge=1)
    model: str | None = None
    mode: Literal['guided', 'socratic', 'deep-dive', 'practical'] | None = None
    layout: Literal['reading', 'chat', 'split', 'focus'] | None = None
    zoom: float | None = Field(default=None, ge=.5, le=2)
    sidebar: bool | None = None
    chat_width: int | None = Field(default=None, ge=25, le=70)


@router.patch('/sessions/{sid}')
async def position(sid: str, body: Position, request: Request):
    principal = await require_user(request)
    runtime = request.app.state.runtime
    session_id = parse_id(sid, 'Study session')
    values = body.model_dump(exclude_none=True)
    if 'model' in values:
        await runtime.catalog.resolve(values['model'], await choice_for(runtime, principal.ctx))
    async with runtime.db.tx(principal.ctx) as conn:
        current = await session_row(conn, session_id)
        if 'page' in values:
            item = await library.require_item(conn, current['library_item_id'])
            count = library.structure(runtime.storage, item).get('page_count') or item.get('page_count')
            if count and values['page'] > count:
                raise HTTPException(422, 'Page is outside this document')
        ui = {k: values.pop(k) for k in list(values) if k in UI_FIELDS}
        row = await one(conn, '''UPDATE study_sessions SET page = COALESCE(%s, page), model = COALESCE(%s, model),
                                        mode = COALESCE(%s, mode), ui_state = ui_state || %s, row_version = row_version + 1
                                 WHERE id = %s RETURNING *''',
                        (values.get('page'), values.get('model'), values.get('mode'), Jsonb(ui), session_id))
    return session_out(row)


class Turn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    selected_text: str = Field(default='', max_length=3000)
    intent: Literal['ask', 'explain', 'simplify', 'example', 'summarize'] = 'ask'


@router.post('/sessions/{sid}/chat')
async def chat(sid: str, body: Turn, request: Request):
    principal = await require_user(request)
    runtime = request.app.state.runtime
    session_id = parse_id(sid, 'Study session')
    try:
        async with runtime.db.tx(principal.ctx) as conn:
            state = await session_row(conn, session_id)
            profile = await runtime.profiles.active_id(conn)
            item = await library.get_item(conn, state['library_item_id'], profile)
            if not item or library.status_of(item)[0] != 'ready':
                raise HTTPException(409, 'AI study tools are still being prepared. You can keep reading and try again shortly.')
            await conn.execute(f'''UPDATE messages SET status = 'cancelled' WHERE session_id = %s AND status = 'generating'
                                   AND created_at < now() - interval '{STALE_REPLY}' ''', (session_id,))
            question = await one(conn, '''INSERT INTO messages (workspace_id, session_id, author_id, role, content, status)
                                          VALUES (%s, %s, %s, 'user', %s, 'complete') RETURNING id''',
                                 (principal.workspace_id, session_id, principal.user_id, body.text))
            answer = await one(conn, '''INSERT INTO messages (workspace_id, session_id, role, content, status, model)
                                        VALUES (%s, %s, 'assistant', '', 'generating', %s) RETURNING id''',
                               (principal.workspace_id, session_id, state['model']))
            await conn.execute('UPDATE study_sessions SET updated_at = now() WHERE id = %s', (session_id,))
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, 'A reply is already being generated for this session') from None
    choice = await choice_for(runtime, principal.ctx)
    user_id, answer_id = question['id'], answer['id']
    document_id = state['library_item_id'].hex

    def event(kind, **data):
        return 'data: '+json.dumps({'type': kind, **data})+'\n\n'

    async def save(content, sink, status):
        async with runtime.db.tx(principal.ctx) as conn:
            await conn.execute('UPDATE messages SET content = %s, status = %s WHERE id = %s', (content, status, answer_id))
            if sink:
                async with conn.cursor() as cur:
                    await cur.executemany('''INSERT INTO citations (message_id, ordinal, workspace_id, library_item_id, blob_id,
                                                                    revision_id, chunk_id, page, label_snapshot)
                                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING''',
                                          [(answer_id, c['id'], principal.workspace_id, state['library_item_id'], c['blob_id'],
                                            c['revision_id'], c['chunk_id'], c['page'], c['label']) for c in sink])

    async def generate():
        content, sink, status = '', [], 'cancelled'
        try:
            yield event('start', message_id=answer_id.hex, user_id=user_id.hex)
            yield event('status', text='Reading your source…')
            response = await complete(runtime, ChatRequest(model=DOC_PREFIX+document_id+'--'+state['model'], stream=True,
                messages=[Message(role='user', content=body.text)], current_page=state['page'],
                selected_text=body.selected_text, study_mode=state['mode'], intent=body.intent), principal.ctx, sink, choice)
            buffer = ''
            async for piece in response.body_iterator:
                buffer += piece.decode() if isinstance(piece, bytes) else piece
                while '\n\n' in buffer:
                    block, buffer = buffer.split('\n\n', 1)
                    if not block.startswith('data: {'):
                        continue
                    delta = json.loads(block[6:])['choices'][0]['delta'].get('content', '')
                    if delta.startswith('\n\n---\nSources retrieved'):
                        yield event('citations', citations=[{k: c[k] for k in ('id', 'label', 'page', 'document_id', 'url')}
                                                            for c in sink])
                    elif delta:
                        content += delta
                        yield event('text', text=delta)
            status = 'failed' if 'StudyHub could not complete this request:' in content else 'complete'
            yield event('done', status=status)
        except asyncio.CancelledError:
            raise
        except Exception:
            status = 'failed'
            yield event('error', text='The reply was interrupted. Please try again.')
        finally:
            # Shielded: a disconnecting browser must not leave the reply stuck in "generating".
            await asyncio.shield(save(content, sink if status == 'complete' else [], status))

    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
