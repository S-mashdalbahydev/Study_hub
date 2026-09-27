"""Library items and the content behind them: blobs, processing revisions, embeddings.

A library item is what a person sees and owns (through their workspace). It points at a
content blob (immutable bytes, deduplicated inside the workspace) and at the processing
revision it currently reads from. Parser output and vectors are versioned beneath that,
so changing the parser or the embedding model never rewrites what old citations point at.
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import jobs
from .db import one, rows, value, vector_literal
from .ingestion import MIME, UnreadableDocument, document_title, parse_document, processing_config, shallow_chunks
from .providers import ProviderError
from .structure import build_structure, section_rows

log = logging.getLogger('uvicorn.error')
NO_PROFILE = -1
SOURCE_TYPE = {'upload': 'uploaded', 'folder_scan': 'uploaded',
               'research_book': 'research_book', 'learning_companion': 'learning_companion'}


class Profiles:
    """Resolves the active embedding profile. The model's digest is cached briefly."""

    def __init__(self, embeddings, ttl=30):
        self.embeddings, self.ttl = embeddings, ttl
        self._key, self._expires = None, 0

    async def key(self):
        if time.monotonic() >= self._expires:
            self._key = await self.embeddings.profile_key()
            self._expires = time.monotonic()+self.ttl
        return self._key

    async def active_id(self, conn):
        """The configured model's profile id.

        None means the model could not be asked (Ollama down): callers accept any ready set.
        NO_PROFILE means the model is known but nothing is embedded with it yet: no set matches."""
        try:
            provider, model, digest, prep = await self.key()
        except Exception:
            return None
        found = await value(conn, '''SELECT id FROM embedding_profiles WHERE provider = %s AND model = %s
                                     AND model_digest = %s AND preprocessing_fingerprint = %s''',
                            (provider, model, digest, prep))
        return NO_PROFILE if found is None else found

    async def ensure(self, conn, dimensions):
        provider, model, digest, prep = await self.key()
        await conn.execute('''INSERT INTO embedding_profiles (provider, model, model_digest, dimensions, preprocessing_fingerprint)
                              VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING''', (provider, model, digest, dimensions, prep))
        row = await one(conn, '''SELECT id, dimensions FROM embedding_profiles WHERE provider = %s AND model = %s
                                 AND model_digest = %s AND preprocessing_fingerprint = %s''', (provider, model, digest, prep))
        if row['dimensions'] != dimensions:
            raise jobs.PermanentFailure('The embedding model returned vectors of an unexpected size.')
        return row['id']


# ------------------------------------------------------------------ reads

ITEM_SQL = '''
SELECT li.id, li.title, li.original_filename AS filename, b.kind, li.origin, li.created_at, li.updated_at,
       li.blob_id, li.current_revision_id, li.research_job_id, li.state, li.title_locked,
       b.object_key, b.mime_type, b.size_bytes,
       r.state AS revision_state, r.page_count, r.artifact_manifest,
       es.id AS set_id, es.state AS set_state, es.expected_count, es.profile_id,
       j.state AS job_state, j.stage AS job_stage, j.error AS job_error
FROM library_items li
JOIN content_blobs b ON b.workspace_id = li.workspace_id AND b.id = li.blob_id
LEFT JOIN processing_revisions r ON r.id = li.current_revision_id
LEFT JOIN LATERAL (
  SELECT id, state, expected_count, profile_id FROM embedding_sets
  WHERE revision_id = li.current_revision_id AND (%(profile)s::smallint IS NULL OR profile_id = %(profile)s)
  ORDER BY (state = 'ready') DESC, created_at DESC LIMIT 1) es ON true
LEFT JOIN LATERAL (
  SELECT state, stage, error FROM jobs
  WHERE target_type = 'revision'
    AND target_id IN (SELECT id FROM processing_revisions WHERE blob_id = li.blob_id)
  ORDER BY created_at DESC LIMIT 1) j ON true
'''


def status_of(row):
    """Collapse revision, vectors and job state into the Library's status vocabulary."""
    if row['revision_state'] == 'ready' and row['set_state'] == 'ready':
        return 'ready', None
    job = row['job_state']
    if job == 'running':
        return row['job_stage'] or 'parsing', None
    if job in ('failed', 'cancelled'):
        return 'failed', row['job_error'] or 'Processing failed. Press Refresh to retry.'
    if row['revision_state'] == 'ready':
        if job == 'queued':
            return 'parsed', None
        return 'parsed', 'The search index needs rebuilding for the current embedding model. Press Refresh.'
    return 'discovered', None


def document(row):
    status, error = status_of(row)
    return {'id': row['id'].hex, 'title': row['title'], 'filename': row['filename'], 'kind': row['kind'],
            'status': status, 'error': error, 'chunk_count': row['expected_count'] or 0,
            'created_at': row['created_at'].isoformat(), 'updated_at': row['updated_at'].isoformat(),
            'source_type': SOURCE_TYPE[row['origin']]}


async def list_items(conn, profile_id):
    return await rows(conn, ITEM_SQL+" WHERE li.state = 'active' ORDER BY li.created_at DESC, li.id",
                      {'profile': profile_id})


async def get_item(conn, item_id, profile_id=None, *, states=('active',)):
    return await one(conn, ITEM_SQL+' WHERE li.id = %(id)s AND li.state = ANY(%(states)s)',
                     {'profile': profile_id, 'id': item_id, 'states': list(states)})


async def require_item(conn, item_id, profile_id=None):
    item = await get_item(conn, item_id, profile_id)
    if not item:
        raise HTTPException(404, 'Document not found')
    return item


async def revision_chunks(conn, revision_id, page=None):
    if not revision_id:
        return []
    if page is None:
        return await rows(conn, '''SELECT id, ordinal AS chunk_index, page, text FROM chunks
                                   WHERE revision_id = %s ORDER BY ordinal''', (revision_id,))
    return await rows(conn, '''SELECT id, ordinal AS chunk_index, page, text FROM chunks
                               WHERE revision_id = %s AND page = %s ORDER BY ordinal''', (revision_id, page))


async def search(conn, item, profile_id, vector, limit):
    """Exact nearest-neighbour search inside one item's current revision and vector set.

    Every row is reached through the item, its revision and a ready set for this profile, so
    an out-of-scope passage cannot be returned. One book is small enough to scan exactly;
    an approximate index belongs to cross-library search."""
    return await rows(conn, '''
        SELECT c.id, c.ordinal AS chunk_index, c.page, c.text, e.embedding <=> %(vector)s::vector AS distance
        FROM embedding_sets s
        JOIN embeddings e ON e.set_id = s.id
        JOIN chunks c ON c.revision_id = e.revision_id AND c.id = e.chunk_id
        WHERE s.revision_id = %(revision)s AND s.profile_id = %(profile)s AND s.state = 'ready'
        ORDER BY distance LIMIT %(limit)s''',
        {'vector': vector_literal(vector), 'revision': item['current_revision_id'], 'profile': profile_id, 'limit': limit})


def structure(storage, item):
    manifest = item.get('artifact_manifest') or {}
    return storage.read_json(manifest.get('structure_key'), None) or \
        {'page_count': item.get('page_count'), 'outline': [], 'figures': [], 'cover': False}


def cover_key(storage, item):
    manifest = item.get('artifact_manifest') or {}
    if manifest.get('cover_key') and storage.exists(manifest['cover_key']):
        return manifest['cover_key']
    generated = f"items/{item['id'].hex}/cover.jpg"
    return generated if storage.exists(generated) else None


# ------------------------------------------------------------------ writes

def hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as handle:
        while piece := handle.read(1024*1024):
            digest.update(piece)
            size += len(piece)
    return digest.digest(), size


async def ingest_file(runtime, ctx, staged: Path, filename, *, origin='upload', research_job_id=None,
                      output_slot=None, title=None):
    """Adopt a verified local file as a library item. Returns (item_id, created).

    The same bytes in the same workspace attach to one blob. If a live item already holds
    those bytes, that item is returned instead of a duplicate (so a retried upload is safe)."""
    kind = Path(filename).suffix.lower().lstrip('.')
    sha, size = await asyncio.to_thread(hash_file, staged)
    parser, parser_version, config, fingerprint = processing_config(kind, runtime.settings.chunk_chars)
    storage, db = runtime.storage, runtime.db
    try:
        for attempt in range(3):
            try:
                return await _attach(runtime, ctx, staged, sha, size, kind, filename, origin, research_job_id,
                                     output_slot, title, parser, parser_version, config, fingerprint)
            except psycopg.errors.UniqueViolation:
                # Someone attached the same bytes at the same moment. Their object has the same
                # key and identical content, so nothing is deleted; the retry finds their blob.
                if attempt == 2:
                    raise
    finally:
        await asyncio.to_thread(Path(staged).unlink, missing_ok=True)


async def _attach(runtime, ctx, staged, sha, size, kind, filename, origin, research_job_id, output_slot, title,
                  parser, parser_version, config, fingerprint):
    storage = runtime.storage
    async with runtime.db.tx(ctx) as conn:
        blob = await one(conn, '''SELECT id, object_key FROM content_blobs
                                  WHERE workspace_id = %s AND sha256 = %s AND state = 'available'
                                  FOR UPDATE''', (ctx.workspace_id, sha))
        if not blob:
            generation = await value(conn, '''SELECT COALESCE(max(generation), 0) + 1 FROM content_blobs
                                              WHERE workspace_id = %s AND sha256 = %s''', (ctx.workspace_id, sha))
            key = f'blobs/{ctx.workspace_id.hex}/{sha.hex()}/{generation}.{kind}'
            # Object first, then the short publishing transaction. A failure after the copy
            # leaves an unreferenced object for reconciliation, never a row without bytes.
            await asyncio.to_thread(storage.copy_in, staged, key)
            blob = await one(conn, '''INSERT INTO content_blobs (workspace_id, sha256, generation, object_key, size_bytes, mime_type, kind)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, object_key''',
                             (ctx.workspace_id, sha, generation, key, size, MIME.get(kind, 'application/octet-stream'), kind))
        live = await one(conn, '''SELECT id FROM library_items WHERE workspace_id = %s AND blob_id = %s AND state = 'active' ''',
                         (ctx.workspace_id, blob['id']))
        if live:
            return live['id'], False
        revision = await one(conn, '''
            INSERT INTO processing_revisions (workspace_id, blob_id, parser, parser_version, config_fingerprint, config)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (blob_id, config_fingerprint) DO UPDATE SET config_fingerprint = EXCLUDED.config_fingerprint
            RETURNING id, state''', (ctx.workspace_id, blob['id'], parser, parser_version, fingerprint, Jsonb(config)))
        item = await one(conn, '''
            INSERT INTO library_items (workspace_id, created_by, blob_id, current_revision_id, title, title_locked,
                                       original_filename, origin, research_job_id, output_slot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
            (ctx.workspace_id, ctx.user_id, blob['id'], revision['id'] if revision['state'] == 'ready' else None,
             (title or Path(filename).stem)[:300] or 'Untitled', bool(title), filename[:300], origin,
             research_job_id, output_slot))
        # Parsing is reused when these bytes were processed before; the job checks the vectors.
        await jobs.enqueue(conn, workspace_id=ctx.workspace_id, queue='ingest', kind='ingest',
                           target_type='revision', target_id=revision['id'], requested_by=ctx.user_id,
                           dedupe_key=f'ingest:{revision["id"]}')
    return item['id'], True


async def trash(conn, item_id, retention_days):
    row = await one(conn, '''UPDATE library_items SET state = 'trashed', trashed_at = now(),
                             purge_after = now() + make_interval(days => %s), row_version = row_version + 1
                             WHERE id = %s AND state = 'active' RETURNING id''', (retention_days, item_id))
    if not row:
        raise HTTPException(404, 'Document not found')


async def restore(conn, item_id):
    try:
        row = await one(conn, '''UPDATE library_items SET state = 'active', trashed_at = NULL, purge_after = NULL,
                                 row_version = row_version + 1
                                 WHERE id = %s AND state = 'trashed' RETURNING id''', (item_id,))
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, 'A live copy of this file is already in your library. Keep that copy, or move it to trash before restoring this one.') from None
    if not row:
        raise HTTPException(404, 'Nothing to restore: the document is not in the trash.')


async def reindex(conn, ctx, profile_id, settings):
    """Refresh: retry failures and index items whose vectors predate the current model."""
    candidates = await rows(conn, '''
        SELECT DISTINCT b.id AS blob_id, b.kind, li.current_revision_id
        FROM library_items li JOIN content_blobs b ON b.workspace_id = li.workspace_id AND b.id = li.blob_id
        WHERE li.state = 'active' ''')
    queued = 0
    for item in candidates:
        parser, parser_version, config, fingerprint = processing_config(item['kind'], settings.chunk_chars)
        revision = await one(conn, '''
            INSERT INTO processing_revisions (workspace_id, blob_id, parser, parser_version, config_fingerprint, config)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (blob_id, config_fingerprint) DO UPDATE SET config_fingerprint = EXCLUDED.config_fingerprint
            RETURNING id, state''', (ctx.workspace_id, item['blob_id'], parser, parser_version, fingerprint, Jsonb(config)))
        # Migrated revisions keep their own fingerprint; only re-parse when nothing is readable.
        target = item['current_revision_id'] or revision['id']
        ready = await value(conn, '''SELECT 1 FROM embedding_sets WHERE revision_id = %s AND state = 'ready'
                                     AND (%s::smallint IS NULL OR profile_id = %s)''', (target, profile_id, profile_id))
        if ready and profile_id is not None:
            continue
        await jobs.enqueue(conn, workspace_id=ctx.workspace_id, queue='ingest', kind='ingest', target_type='revision',
                           target_id=target, requested_by=ctx.user_id, dedupe_key=f'ingest:{target}')
        queued += 1
    return queued


# ------------------------------------------------------------------ the ingestion job

async def handle_ingest(runtime, db, job):
    settings, storage = runtime.settings, runtime.storage
    async with db.tx(job.ctx) as conn:
        revision = await one(conn, '''SELECT r.*, b.object_key, b.kind FROM processing_revisions r
                                      JOIN content_blobs b ON b.workspace_id = r.workspace_id AND b.id = r.blob_id
                                      WHERE r.id = %s''', (job.target_id,))
        if not revision:
            raise jobs.PermanentFailure('The file for this job no longer exists.')
        live = await rows(conn, '''SELECT id, title, original_filename FROM library_items
                                   WHERE blob_id = %s AND state = 'active' ORDER BY created_at''', (revision['blob_id'],))
    if not live:
        return  # every item using these bytes was deleted; there is nothing to publish into
    if revision['state'] != 'ready':
        try:
            await parse_revision(runtime, db, job, revision, live[0])
        except UnreadableDocument as exc:
            raise jobs.PermanentFailure(str(exc)) from None
    await embed_revision(runtime, db, job, revision, live[0]['title'])


async def parse_revision(runtime, db, job, revision, item):
    settings, storage = runtime.settings, runtime.storage
    await jobs.set_stage(db, job, 'parsing')
    prefix = f"revisions/{revision['id'].hex}/a{job.attempts}"
    with storage.local_path(revision['object_key']) as original:
        parsed = await parse_document(runtime.client, settings, revision['kind'], original, item['original_filename'])
        await asyncio.to_thread(storage.put_json, prefix+'/parsed.json', parsed)
        try:
            info, manifest = await asyncio.to_thread(build_structure, storage, revision['kind'],
                                                     original if revision['kind'] == 'pdf' else None, parsed, prefix)
        except Exception:
            log.warning('structure unavailable for revision %s', revision['id'])
            info, manifest = {'page_count': None, 'outline': []}, {}
    manifest['parsed_key'] = prefix+'/parsed.json'
    title = document_title(parsed, Path(item['original_filename']).stem)
    await jobs.set_stage(db, job, 'chunking')
    chunks = shallow_chunks(parsed, settings.chunk_chars)
    async with jobs.fenced(db, job) as conn:
        await write_structure(conn, job.workspace_id, revision['id'], info.get('outline') or [], chunks)
        await conn.execute('''UPDATE processing_revisions SET state = 'ready', ready_at = now(), title = %s, page_count = %s,
                              artifact_manifest = %s, error = NULL WHERE id = %s''',
                           (title, info.get('page_count'), Jsonb(manifest), revision['id']))
        await conn.execute('''UPDATE library_items SET current_revision_id = %s,
                                     title = CASE WHEN title_locked THEN title ELSE %s END
                              WHERE blob_id = %s AND state = 'active' ''', (revision['id'], title, revision['blob_id']))
        if info.get('page_count'):
            await conn.execute("INSERT INTO usage_events (workspace_id, kind, quantity, job_id) VALUES (%s, 'pages_parsed', %s, %s)",
                               (job.workspace_id, info['page_count'], job.id))
    revision['state'] = 'ready'


async def write_structure(conn, workspace_id, revision_id, outline, chunks):
    """Sections in document order (parents first), then chunks assigned to the section they fall in."""
    ids, starts = {}, []
    for row in section_rows(outline):
        section = await one(conn, '''INSERT INTO sections (workspace_id, revision_id, parent_id, slug, title, level, position, page_start)
                                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                            (workspace_id, revision_id, ids.get(row['parent_slug']), row['slug'], row['title'],
                             row['level'], row['position'], row['page_start']))
        ids[row['slug']] = section['id']
        starts.append((row['page_start'], section['id']))

    def section_for(page):
        found = None
        for start, section_id in starts:
            if start <= page:
                found = section_id
        return found

    async with conn.cursor() as cur:
        await cur.executemany('''INSERT INTO chunks (workspace_id, revision_id, section_id, ordinal, page, text)
                                 VALUES (%s, %s, %s, %s, %s, %s)''',
                              [(workspace_id, revision_id, section_for(c['page']) if c.get('page') else None, index,
                                c.get('page') or None, c['text']) for index, c in enumerate(chunks)])


async def embed_revision(runtime, db, job, revision, title, batch_size=16):
    await jobs.set_stage(db, job, 'embedding')
    async with db.tx(job.ctx) as conn:
        chunks = await rows(conn, 'SELECT id, text FROM chunks WHERE revision_id = %s ORDER BY ordinal', (revision['id'],))
    if not chunks:
        raise jobs.PermanentFailure('No readable text was extracted. Try a text PDF or check Docling OCR support for this file.')
    probe = await runtime.embeddings.embed([chunks[0]['text']], title=title)
    async with db.tx() as conn:
        profile_id = await runtime.profiles.ensure(conn, len(probe[0]))
    async with jobs.fenced(db, job) as conn:
        await conn.execute('''INSERT INTO embedding_sets (workspace_id, revision_id, profile_id, expected_count)
                              VALUES (%s, %s, %s, %s) ON CONFLICT (revision_id, profile_id) DO NOTHING''',
                           (job.workspace_id, revision['id'], profile_id, len(chunks)))
        vector_set = await one(conn, 'SELECT id, state FROM embedding_sets WHERE revision_id = %s AND profile_id = %s',
                               (revision['id'], profile_id))
        done = {r['chunk_id'] for r in await rows(conn, 'SELECT chunk_id FROM embeddings WHERE set_id = %s', (vector_set['id'],))}
    if vector_set['state'] == 'ready':
        await finish_outputs(db, job, revision)
        return
    pending = [c for c in chunks if c['id'] not in done]
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start+batch_size]
        vectors = await runtime.embeddings.embed([c['text'] for c in batch], title=title)
        async with jobs.fenced(db, job) as conn:
            async with conn.cursor() as cur:
                await cur.executemany('''INSERT INTO embeddings (set_id, chunk_id, workspace_id, revision_id, profile_id, embedding)
                                         VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT DO NOTHING''',
                                      [(vector_set['id'], c['id'], job.workspace_id, revision['id'], profile_id, vector_literal(v))
                                       for c, v in zip(batch, vectors)])
            await conn.execute('''UPDATE embedding_sets SET embedded_count = (SELECT count(*) FROM embeddings WHERE set_id = %s)
                                  WHERE id = %s''', (vector_set['id'], vector_set['id']))
    async with jobs.fenced(db, job) as conn:
        ready = await one(conn, '''UPDATE embedding_sets SET state = 'ready', ready_at = now()
                                   WHERE id = %s AND embedded_count = expected_count RETURNING id''', (vector_set['id'],))
        if not ready:
            raise RuntimeError('Not every passage was embedded; the job will resume where it stopped.')
        await conn.execute("INSERT INTO usage_events (workspace_id, kind, quantity, job_id) VALUES (%s, 'chunks_embedded', %s, %s)",
                           (job.workspace_id, len(pending), job.id))
    await finish_outputs(db, job, revision)


async def finish_outputs(db, job, revision):
    """A published Study Book becomes ready once its own index is ready."""
    async with jobs.fenced(db, job) as conn:
        await conn.execute('''UPDATE research_jobs SET status = 'ready', error = NULL,
                                     progress = '{"message": "Your Study Book is ready", "completed": 6, "total": 6}'
                              WHERE status = 'indexing' AND id IN (
                                SELECT research_job_id FROM library_items
                                WHERE blob_id = %s AND state = 'active' AND research_job_id IS NOT NULL)''', (revision['blob_id'],))


async def on_ingest_failure(runtime, db, job, message):
    async with db.tx(job.ctx) as conn:
        await conn.execute('''UPDATE research_jobs SET status = 'failed', error = %s
                              WHERE status = 'indexing' AND id IN (
                                SELECT li.research_job_id FROM library_items li
                                JOIN processing_revisions r ON r.blob_id = li.blob_id
                                WHERE r.id = %s AND li.research_job_id IS NOT NULL)''', (message, job.target_id))
