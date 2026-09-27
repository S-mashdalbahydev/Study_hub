"""Library documents: list, upload, read the original, trash and restore.

"Document" is the API's name for a library item. Every route runs as the signed-in user
inside their active workspace; the database's row-level security enforces the same scope.
"""
import asyncio
import html
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from . import library
from .auth import audit, require_user
from .db import rows
from .ids import parse_id, public
from .ingestion import MAX_BYTES, SUPPORTED

router = APIRouter(prefix='/api')


async def scoped(request):
    principal = await require_user(request)
    return principal, request.app.state.runtime


@router.get('/documents')
async def documents(request: Request):
    principal, runtime = await scoped(request)
    async with runtime.db.tx(principal.ctx) as conn:
        profile = await runtime.profiles.active_id(conn)
        return [library.document(row) for row in await library.list_items(conn, profile)]


@router.get('/documents/{doc_id}')
async def document(doc_id: str, request: Request):
    principal, runtime = await scoped(request)
    async with runtime.db.tx(principal.ctx) as conn:
        profile = await runtime.profiles.active_id(conn)
        return library.document(await library.require_item(conn, parse_id(doc_id, 'Document'), profile))


@router.get('/documents/{doc_id}/events')
async def events(doc_id: str, request: Request):
    principal, runtime = await scoped(request)
    async with runtime.db.tx(principal.ctx) as conn:
        item = await library.require_item(conn, parse_id(doc_id, 'Document'))
        found = await rows(conn, '''SELECT state, stage, error, attempts, created_at, updated_at, finished_at FROM jobs
                                    WHERE target_type = 'revision'
                                      AND target_id IN (SELECT id FROM processing_revisions WHERE blob_id = %s)
                                    ORDER BY created_at''', (item['blob_id'],))
    return public(found)


@router.post('/library/refresh', status_code=202)
async def refresh(request: Request):
    """Retry failed processing and re-index documents embedded with an older model."""
    principal, runtime = await scoped(request)
    try:
        await runtime.profiles.key()
    except Exception:
        pass
    async with runtime.db.tx(principal.ctx) as conn:
        profile = await runtime.profiles.active_id(conn)
        queued = await library.reindex(conn, principal.ctx, profile, runtime.settings)
    return {'queued': queued, 'warnings': []}


@router.post('/documents/upload', status_code=202)
async def upload(request: Request, file: UploadFile = File(...)):
    principal, runtime = await scoped(request)
    filename = Path((file.filename or '').replace('\\', '/')).name
    if Path(filename).suffix.lower() not in SUPPORTED or filename.startswith('.'):
        raise HTTPException(400, 'Supported files: PDF, Markdown, TXT and DOCX')
    staged = runtime.storage.staging_file()
    total = 0
    try:
        with staged.open('wb') as target:
            while piece := await file.read(1024*1024):
                total += len(piece)
                if total > MAX_BYTES:
                    raise HTTPException(413, 'Maximum file size is 128 MB')
                await asyncio.to_thread(target.write, piece)
        if not total:
            raise HTTPException(400, 'That file is empty.')
        item_id, created = await library.ingest_file(runtime, principal.ctx, staged, filename)
        async with runtime.db.tx(principal.ctx) as conn:
            await audit(conn, 'document.uploaded' if created else 'document.upload_deduplicated', actor=principal.user_id,
                        workspace=principal.workspace_id, target_type='library_item', target_id=item_id,
                        request=request, bytes=total)
        return {'document_ids': [item_id.hex], 'created': created, 'warnings': []}
    finally:
        staged.unlink(missing_ok=True)
        await file.close()


@router.get('/documents/{doc_id}/source')
async def source(doc_id: str, request: Request):
    principal, runtime = await scoped(request)
    async with runtime.db.tx(principal.ctx) as conn:
        item = await library.require_item(conn, parse_id(doc_id, 'Document'))
    with runtime.storage.local_path(item['object_key']) as path:
        return FileResponse(path, filename=item['filename'], content_disposition_type='inline',
                            media_type=item['mime_type'], headers={'Cache-Control': 'private, max-age=3600'})


@router.get('/documents/{doc_id}/text', response_class=HTMLResponse)
async def source_text(doc_id: str, request: Request):
    principal, runtime = await scoped(request)
    async with runtime.db.tx(principal.ctx) as conn:
        item = await library.require_item(conn, parse_id(doc_id, 'Document'))
        chunks = await library.revision_chunks(conn, item['current_revision_id'])
    body = ''.join(f'<section id="chunk-{c["chunk_index"]}"><h2>Excerpt {c["chunk_index"]+1}</h2><pre>{html.escape(c["text"])}</pre></section>'
                   for c in chunks)
    return ('<!doctype html><meta charset="utf-8"><title>Source excerpts</title><style>body{font:16px system-ui;max-width:900px;'
            'margin:40px auto}pre{white-space:pre-wrap;font:inherit}</style><h1>'+html.escape(item['title'])+'</h1>'+body)


@router.delete('/documents/{doc_id}', status_code=204)
async def trash(doc_id: str, request: Request):
    """Access ends immediately. The bytes stay until the trash retention period ends."""
    principal, runtime = await scoped(request)
    item_id = parse_id(doc_id, 'Document')
    async with runtime.db.tx(principal.ctx) as conn:
        await library.trash(conn, item_id, runtime.settings.trash_retention_days)
        await audit(conn, 'document.trashed', actor=principal.user_id, workspace=principal.workspace_id,
                    target_type='library_item', target_id=item_id, request=request)
    return Response(status_code=204)


@router.post('/documents/{doc_id}/restore')
async def restore(doc_id: str, request: Request):
    principal, runtime = await scoped(request)
    item_id = parse_id(doc_id, 'Document')
    async with runtime.db.tx(principal.ctx) as conn:
        await library.restore(conn, item_id)
        await audit(conn, 'document.restored', actor=principal.user_id, workspace=principal.workspace_id,
                    target_type='library_item', target_id=item_id, request=request)
        profile = await runtime.profiles.active_id(conn)
        return library.document(await library.require_item(conn, item_id, profile))


@router.get('/trash')
async def trashed(request: Request):
    principal, runtime = await scoped(request)
    async with runtime.db.tx(principal.ctx) as conn:
        found = await rows(conn, '''SELECT id, title, original_filename AS filename, trashed_at, purge_after
                                    FROM library_items WHERE state = 'trashed' ORDER BY trashed_at DESC''')
    return public(found)
