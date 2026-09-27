"""Move the single-user SQLite + Chroma data into one account's personal workspace.

    python -m app.migrate_legacy --email you@example.com [--dry-run]

Sign up first; this assigns everything to that account. It runs as the schema owner in
one database transaction and commits only if every hard check passes. The SQLite file,
the Chroma volume and the original folders are only read, never changed, so they remain
the rollback path. Vectors are copied from Chroma as they are; nothing is re-embedded.
Object keys are derived from the legacy ids, so a repeated dry run overwrites its own files.
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid

import httpx
import psycopg
from psycopg.types.json import Jsonb

from .ingestion import MIME, processing_config
from .db import vector_literal
from .settings import Settings
from .storage import LocalStorage
from .structure import section_rows

NAMESPACE = uuid.UUID('6f1e6c2a-4d1b-4c55-9b7e-2f3f8a0d9e11')
LOCALHOST = 'http://localhost:8000/api/documents/'


class MigrationError(RuntimeError):
    pass


def derived(kind, legacy_id):
    return uuid.uuid5(NAMESPACE, f'{kind}:{legacy_id}')


def preprocessing(model):
    if model.startswith('embeddinggemma'):
        return 'embeddinggemma-task-prompts-v1'
    if model.startswith('nomic-embed-text'):
        return 'nomic-search-prefix-v1'
    return 'plain-v1'


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def relink(text):
    """Stored drafts used absolute localhost links; the new app builds relative ones."""
    return text.replace(LOCALHOST, '/backend/api/documents/') if isinstance(text, str) else text


def chroma_vectors(client, base, collection_id, doc_id):
    vectors, offset = {}, 0
    while True:
        response = client.post(f'{base}/{collection_id}/get', json={'include': ['embeddings'], 'limit': 500, 'offset': offset})
        if response.status_code == 404:
            raise MigrationError(f'Chroma collection for {doc_id} is missing.')
        response.raise_for_status()
        data = response.json()
        for key, vector in zip(data['ids'], data['embeddings']):
            vectors[int(key.rsplit(':', 1)[1])] = vector
        if len(data['ids']) < 500:
            return vectors
        offset += 500


def migrate(email, *, sqlite_path, owner_url, chroma_url, settings, dry_run=False):
    storage = LocalStorage(settings.objects)
    # Work from a private copy: the source may sit on a read-only mount, and WAL mode needs
    # writable sidecar files even for readers. The copy includes any -wal/-shm files.
    snapshot = Path(tempfile.mkdtemp(prefix='studyhub-legacy-'))
    for suffix in ('', '-wal', '-shm'):
        source = Path(str(sqlite_path)+suffix)
        if source.exists():
            shutil.copyfile(source, snapshot/('studyhub.sqlite3'+suffix))
    if not (snapshot/'studyhub.sqlite3').exists():
        raise MigrationError(f'No legacy database at {sqlite_path}.')
    legacy = sqlite3.connect(snapshot/'studyhub.sqlite3')
    legacy.row_factory = sqlite3.Row
    table = lambda name: [dict(r) for r in legacy.execute(f'SELECT * FROM {name} ORDER BY rowid')]  # noqa: E731
    tables = {name for (name,) in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    get = lambda name: table(name) if name in tables else []  # noqa: E731
    report = {'source': {}, 'migrated': {}, 'checks': [], 'warnings': []}
    chroma = httpx.Client(base_url='', timeout=60, trust_env=False)
    chroma_base = chroma_url.rstrip('/')+'/api/v2/tenants/default_tenant/databases/default_database/collections'
    data = settings.data

    def check(name, ok, detail=''):
        report['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        if not ok:
            raise MigrationError(f'{name} failed: {detail}')

    with psycopg.connect(owner_url) as conn:
        with conn.transaction():
            account = conn.execute('''SELECT u.id AS user_id, w.id AS workspace_id FROM auth_identities i
                                      JOIN users u ON u.id = i.user_id AND u.status = 'active'
                                      JOIN workspaces w ON w.created_by = u.id AND w.kind = 'personal' AND w.status = 'active'
                                      WHERE i.email = %s AND i.revoked_at IS NULL''', (email.lower(),)).fetchone()
            if not account:
                raise MigrationError(f'No active account with a personal workspace for {email}. Sign up first.')
            user, ws = account
            documents = get('documents')
            report['source'] = {name: len(get(name)) for name in sorted(tables)}
            existing = conn.execute('SELECT count(*) FROM library_items WHERE id = ANY(%s)',
                                    ([uuid.UUID(d['id']) for d in documents],)).fetchone()[0]
            if existing:
                raise MigrationError('This data has already been migrated (library items with these ids exist).')

            # -------- embedding profile(s), from the identity each document was indexed with
            profiles = {}

            def profile_for(config):
                key = (config['provider'], config['model'], config['digest'])
                if key not in profiles:
                    profiles[key] = None
                return key

            # -------- research jobs first: generated items point at them
            books = {r['document_id']: r for r in get('research_books')}
            jobs = get('research_jobs')
            for job in jobs:
                conn.execute('''INSERT INTO research_jobs (id, workspace_id, created_by, mode, material_type, query, urls, depth, level,
                                    goal, status, review_state, progress, outline, content_sections, draft, error, created_at, updated_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(job['id']), ws, user, job['mode'], job['material_type'], job['query'], loads(job['urls'], []),
                              job['depth'], job['level'], job['goal'], job['status'],
                              next((b['research_state'] for b in books.values() if b['job_id'] == job['id']), 'draft'),
                              Jsonb(loads(job['progress'], {})), Jsonb(loads(job['outline'], [])),
                              Jsonb(json.loads(relink(job.get('content_sections') or '[]'))), relink(job['draft']) or '',
                              job['error'], job['created_at'], job['updated_at']))
            report['migrated']['research_jobs'] = len(jobs)

            # -------- documents: blob, revision, structure, chunks, vectors, item
            chunks_by_doc = {}
            for row in get('chunks'):
                chunks_by_doc.setdefault(row['document_id'], []).append(row)
            revisions, item_kinds, sections_by_doc = {}, {}, {}
            embedded = 0
            for doc in documents:
                doc_id, kind = doc['id'], doc['kind']
                original = data/'originals'/f'{doc_id}.{kind}'
                check(f'original present: {doc["filename"]}', original.exists(), str(original))
                content = original.read_bytes()
                check(f'original hash: {doc["filename"]}', hashlib.sha256(content).hexdigest() == doc['content_hash'], doc_id)
                sha = bytes.fromhex(doc['content_hash'])
                key = f'blobs/{ws.hex}/{sha.hex()}/1.{kind}'
                storage.put_bytes(key, content)
                blob = conn.execute('''INSERT INTO content_blobs (workspace_id, sha256, object_key, size_bytes, mime_type, kind, created_at)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                                    (ws, sha, key, len(content), MIME.get(kind, 'application/octet-stream'), kind, doc['created_at'])).fetchone()[0]
                config = loads(doc['embedding_config'], None)
                check(f'indexed: {doc["filename"]}', doc['status'] == 'ready' and config and doc['collection_id'], doc['status'])
                parser, parser_version, pconfig, fingerprint = processing_config(kind, config.get('chunk_chars', 1400))
                revision = derived('revision', doc_id)
                prefix = f'revisions/{revision.hex}/legacy'
                manifest = {}
                parsed = data/'parsed'/f'{doc_id}.json'
                if parsed.exists():
                    storage.copy_in(parsed, prefix+'/parsed.json')
                    manifest['parsed_key'] = prefix+'/parsed.json'
                structure = loads((data/'previews'/f'{doc_id}.json').read_text(), {}) if (data/'previews'/f'{doc_id}.json').exists() else {}
                structure.setdefault('outline', [])
                structure.setdefault('figures', [])
                storage.put_json(prefix+'/structure.json', structure)
                manifest['structure_key'] = prefix+'/structure.json'
                if (data/'previews'/f'{doc_id}.jpg').exists():
                    storage.copy_in(data/'previews'/f'{doc_id}.jpg', prefix+'/cover.jpg')
                    manifest['cover_key'] = prefix+'/cover.jpg'
                conn.execute('''INSERT INTO processing_revisions (id, workspace_id, blob_id, parser, parser_version, config_fingerprint, config,
                                    state, title, page_count, artifact_manifest, created_at, ready_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,'ready',%s,%s,%s,%s,%s)''',
                             (revision, ws, blob, parser, parser_version, fingerprint, Jsonb({**pconfig, 'migrated_from': 'sqlite'}),
                              doc['title'], structure.get('page_count'), Jsonb(manifest), doc['created_at'], doc['updated_at']))
                slugs, starts = {}, []
                for srow in section_rows(structure['outline']):
                    sid = conn.execute('''INSERT INTO sections (workspace_id, revision_id, parent_id, slug, title, level, position, page_start)
                                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                                       (ws, revision, slugs.get(srow['parent_slug']), srow['slug'], srow['title'], srow['level'],
                                        srow['position'], srow['page_start'])).fetchone()[0]
                    slugs[srow['slug']] = sid
                    starts.append((srow['page_start'], sid))
                sections_by_doc[doc_id] = slugs
                rows = sorted(chunks_by_doc.get(doc_id, []), key=lambda r: r['chunk_index'])
                check(f'chunk count: {doc["filename"]}', len(rows) == doc['chunk_count'] and
                      [r['chunk_index'] for r in rows] == list(range(len(rows))), f'{len(rows)} vs {doc["chunk_count"]}')
                chunk_ids = []
                for r in rows:
                    section = None
                    for start, sid in starts:
                        if r['page'] and start <= r['page']:
                            section = sid
                    chunk_ids.append(conn.execute('''INSERT INTO chunks (workspace_id, revision_id, section_id, ordinal, page, text)
                                                     VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',
                                                  (ws, revision, section, r['chunk_index'], r['page'] or None, r['text'])).fetchone()[0])
                vectors = chroma_vectors(chroma, chroma_base, doc['collection_id'], doc_id)
                check(f'vectors: {doc["filename"]}', sorted(vectors) == list(range(len(rows))), f'{len(vectors)} vectors for {len(rows)} chunks')
                pkey = profile_for(config)
                if profiles[pkey] is None:
                    dims = len(next(iter(vectors.values())))
                    conn.execute('''INSERT INTO embedding_profiles (provider, model, model_digest, dimensions, preprocessing_fingerprint)
                                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''', (*pkey, dims, preprocessing(pkey[1])))
                    profiles[pkey] = conn.execute('''SELECT id FROM embedding_profiles WHERE provider=%s AND model=%s AND model_digest=%s
                                                     AND preprocessing_fingerprint=%s''', (*pkey, preprocessing(pkey[1]))).fetchone()[0]
                vector_set = conn.execute('''INSERT INTO embedding_sets (workspace_id, revision_id, profile_id, state, expected_count,
                                                 embedded_count, ready_at) VALUES (%s,%s,%s,'ready',%s,%s,%s) RETURNING id''',
                                          (ws, revision, profiles[pkey], len(rows), len(rows), doc['updated_at'])).fetchone()[0]
                with conn.cursor() as cur:
                    cur.executemany('''INSERT INTO embeddings (set_id, chunk_id, workspace_id, revision_id, profile_id, embedding)
                                       VALUES (%s,%s,%s,%s,%s,%s::vector)''',
                                    [(vector_set, chunk_ids[i], ws, revision, profiles[pkey], vector_literal(vectors[i])) for i in range(len(rows))])
                embedded += len(rows)
                book = books.get(doc_id)
                job = next((j for j in jobs if book and j['id'] == book['job_id']), None)
                origin = ('learning_companion' if job['material_type'] == 'learning_companion' else 'research_book') if job else 'upload'
                conn.execute('''INSERT INTO library_items (id, workspace_id, created_by, blob_id, current_revision_id, title, original_filename,
                                    origin, research_job_id, output_slot, created_at, updated_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(doc_id), ws, user, blob, revision, doc['title'][:300] or doc['filename'], doc['filename'], origin,
                              uuid.UUID(job['id']) if job else None, 0 if job else None, doc['created_at'], doc['updated_at']))
                conn.execute('''INSERT INTO jobs (workspace_id, requested_by, queue, kind, target_type, target_id, state, stage,
                                              attempts, created_at, finished_at, payload)
                                          VALUES (%s,%s,'ingest','ingest','revision',%s,'succeeded','embedding',1,%s,%s,%s) RETURNING id''',
                                       (ws, user, revision, doc['created_at'], doc['updated_at'], Jsonb({'migrated_from': 'sqlite'})))
                revisions[doc_id] = (revision, blob)
                item_kinds[doc_id] = kind
            report['migrated'].update({'library_items': len(documents), 'content_blobs': len(documents), 'embeddings': embedded,
                                       'chunks': sum(len(v) for k, v in chunks_by_doc.items() if k in revisions)})
            events = get('processing_events')
            with conn.cursor() as cur:
                cur.executemany('''INSERT INTO audit_events (workspace_id, actor_id, action, target_type, target_id, metadata, at)
                                   VALUES (%s,%s,'legacy.processing','library_item',%s,%s,%s)''',
                                [(ws, user, uuid.UUID(e['document_id']), Jsonb({'status': e['status']}), e['at'])
                                 for e in events if e['document_id'] in revisions])
            report['migrated']['processing_events'] = sum(1 for e in events if e['document_id'] in revisions)
            for job in jobs:
                if job.get('source_document_id') in revisions:
                    conn.execute('UPDATE research_jobs SET source_item_id = %s WHERE id = %s',
                                 (uuid.UUID(job['source_document_id']), uuid.UUID(job['id'])))
                    for doc_id, book in books.items():
                        if book['job_id'] == job['id'] and doc_id in revisions:
                            conn.execute('INSERT INTO item_derivations (workspace_id, item_id, source_item_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                                         (ws, uuid.UUID(doc_id), uuid.UUID(job['source_document_id'])))

            # -------- research evidence
            sources = get('research_sources')
            with conn.cursor() as cur:
                cur.executemany('''INSERT INTO research_sources (workspace_id, job_id, id, title, url, author, publisher, published_at,
                                       retrieved_at, source_type, retained, excerpt) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s)''',
                                [(ws, uuid.UUID(s['job_id']), s['id'], s['title'], s['url'], s['author'], s['publisher'],
                                  s['retrieved_at'], s['source_type'], bool(s['retained']), s['excerpt']) for s in sources])
                resources = get('research_resources')
                cur.executemany('''INSERT INTO research_resources (workspace_id, job_id, id, chapter, title, url, resource_type, publisher,
                                       snippet, validation_reason, selected, thumbnail_url, video_count)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                                [(ws, uuid.UUID(r['job_id']), r['id'], r['chapter'], r['title'], r['url'], r['resource_type'], r['publisher'],
                                  r.get('snippet') or '', r.get('validation_reason') or '', bool(r.get('selected')),
                                  r.get('thumbnail') or '', r.get('video_count')) for r in resources])
            report['migrated'].update({'research_sources': len(sources), 'research_resources': len(resources)})

            # -------- reading sessions, messages and citations
            sessions = get('sessions')
            for s in sessions:
                check(f'session document exists: {s["id"]}', s['document_id'] in revisions, s['document_id'])
                conn.execute('''INSERT INTO study_sessions (id, workspace_id, created_by, library_item_id, page, model, mode, ui_state,
                                    created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(s['id']), ws, user, uuid.UUID(s['document_id']), max(1, s['page']), s['model'], s['mode'],
                              Jsonb({'layout': s['layout'], 'zoom': s['zoom'], 'sidebar': bool(s['sidebar']), 'chat_width': s['chat_width']}),
                              s['created_at'], s['updated_at']))
            messages = get('messages')
            known_sessions = {s['id'] for s in sessions}
            citations = unresolved = 0
            for m in messages:
                if m['session_id'] not in known_sessions:
                    report['warnings'].append(f'message {m["id"]} skipped: its session is missing')
                    continue
                conn.execute('''INSERT INTO messages (id, workspace_id, session_id, author_id, role, content, status, created_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(m['id']), ws, uuid.UUID(m['session_id']), user if m['role'] == 'user' else None, m['role'],
                              m['content'], 'cancelled' if m['status'] == 'generating' else m['status'], m['created_at']))
                for ordinal, c in enumerate(loads(m['citations'], []), 1):
                    target = revisions.get(c.get('document_id'))
                    if not target:
                        unresolved += 1
                        continue
                    conn.execute('''INSERT INTO citations (message_id, ordinal, workspace_id, library_item_id, blob_id, revision_id, page, label_snapshot)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                                 (uuid.UUID(m['id']), c.get('id') or ordinal, ws, uuid.UUID(c['document_id']), target[1], target[0],
                                  c.get('page') or None, c.get('label') or f'excerpt {ordinal}'))
                    citations += 1
            report['migrated'].update({'study_sessions': len(sessions), 'messages': len(messages), 'citations': citations})
            if unresolved:
                report['warnings'].append(f'{unresolved} citation(s) pointed at documents that no longer exist and were dropped')

            # -------- study spaces
            spaces = get('learning_sessions')
            for s in spaces:
                conn.execute('''INSERT INTO learning_sessions (id, workspace_id, created_by, title, learning_goal, status, notebook, created_at, updated_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(s['id']), ws, user, s['title'][:160], s['learning_goal'], s['status'],
                              Jsonb(loads(s['notebook'], [])), s['created_at'], s['updated_at']))
            for m in get('learning_materials'):
                check(f'material exists: {m["document_id"]}', m['document_id'] in revisions, m['session_id'])
                conn.execute('''INSERT INTO learning_materials (workspace_id, session_id, library_item_id, material_type, source_url,
                                    timestamp_start, timestamp_end) VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                             (ws, uuid.UUID(m['session_id']), uuid.UUID(m['document_id']), m['material_type'], m['source_url'],
                              m['timestamp_start'], m['timestamp_end']))
            themes = get('learning_themes')
            for t in themes:
                conn.execute('''INSERT INTO learning_themes (id, workspace_id, session_id, title, summary, position, lesson, exercise, citations, resources)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(t['id']), ws, uuid.UUID(t['session_id']), t['title'], t['summary'], t['position'], t['lesson'],
                              t['exercise'], Jsonb(loads(t['citations'], [])), Jsonb(loads(t['resources'], []))))
            matched = unmatched = 0
            for c in get('learning_theme_chapters'):
                section = sections_by_doc.get(c['document_id'], {}).get(c['chapter_id'])
                matched, unmatched = matched+bool(section), unmatched+(not section)
                conn.execute('''INSERT INTO learning_theme_chapters (workspace_id, session_id, theme_id, library_item_id, chapter_key, revision_id,
                                    section_id, match_status, title, page_start, page_end, position, done, done_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END)''',
                             (ws, uuid.UUID(c['session_id']), uuid.UUID(c['theme_id']), uuid.UUID(c['document_id']), c['chapter_id'],
                              revisions[c['document_id']][0] if section else None, section, 'matched' if section else 'unresolved',
                              c['title'], max(1, c['page']), c['end_page'], c['position'], bool(c['done']), bool(c['done'])))
            report['migrated'].update({'learning_sessions': len(spaces), 'learning_themes': len(themes),
                                       'learning_theme_chapters': matched+unmatched, 'chapters_matched_to_outline': matched})
            if unmatched:
                report['warnings'].append(f'{unmatched} chapter pick(s) could not be matched to a book outline; kept as unresolved')
            answers = get('exercise_answers')
            theme_ids = {t['id'] for t in themes}
            notebook_blocks = {s['id']: {b.get('id') for b in loads(s['notebook'], [])} for s in spaces}
            for a in answers:
                if a['block_id'] not in theme_ids and a['block_id'] not in notebook_blocks.get(a['session_id'], set()):
                    report['warnings'].append(f'exercise answer {a["id"]} refers to a block that no longer exists ({a["block_id"]})')
                conn.execute('''INSERT INTO exercise_answers (id, workspace_id, session_id, author_id, block_id, answer, feedback, created_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (uuid.UUID(a['id']), ws, uuid.UUID(a['session_id']), user, a['block_id'], a['answer'], a['feedback'], a['created_at']))
            report['migrated']['exercise_answers'] = len(answers)

            # -------- tutor connections: local models become available to everyone on this install
            providers = get('tutor_providers')
            for p in providers:
                if p['api_key']:
                    report['warnings'].append(f'tutor connection "{p["label"]}" had an API key; re-enter it in Settings (keys are now encrypted)')
                pid = conn.execute('''INSERT INTO tutor_providers (id, scope, workspace_id, label, kind, base_url, model, created_by, created_at)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                                   (uuid.UUID(p['id']), 'system' if p['kind'] == 'ollama' else 'workspace',
                                    None if p['kind'] == 'ollama' else ws, p['label'], p['kind'], p['base_url'], p['model'], user,
                                    p['created_at'])).fetchone()[0]
                if p['active']:
                    conn.execute('UPDATE user_settings SET default_tutor_id = %s WHERE user_id = %s', (pid, user))
            report['migrated']['tutor_providers'] = len(providers)

            # -------- verification against the source, not just counts
            for name, source in (('library_items', 'documents'), ('study_sessions', 'sessions'), ('messages', 'messages'),
                                 ('research_jobs', 'research_jobs'), ('learning_sessions', 'learning_sessions'),
                                 ('exercise_answers', 'exercise_answers')):
                target = conn.execute(f'SELECT count(*) FROM {name} WHERE workspace_id = %s', (ws,)).fetchone()[0]
                check(f'row parity {source} -> {name}', target == report['source'].get(source, 0), f'{target} vs {report["source"].get(source, 0)}')
            text_sum = conn.execute('SELECT coalesce(sum(length(text)), 0) FROM chunks WHERE workspace_id = %s', (ws,)).fetchone()[0]
            legacy_sum = legacy.execute('SELECT coalesce(sum(length(text)), 0) FROM chunks WHERE document_id IN (%s)' %
                                        ','.join('?'*len(revisions)), list(revisions)).fetchone()[0] if revisions else 0
            check('chunk text preserved', text_sum == legacy_sum, f'{text_sum} vs {legacy_sum} characters')
            if dry_run:
                raise _DryRun(report)
    return report


def handover(root):
    """Run as root to read the old root-owned files; give what was written to the app user."""
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        uid = gid = int(os.getenv('STUDYHUB_UID', '10001'))
        for path in [Path(root), *Path(root).rglob('*')]:
            os.chown(path, uid, gid)


class _DryRun(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description='Assign the single-user StudyHub data to one account.')
    parser.add_argument('--email', required=True, help='The account you signed up with')
    parser.add_argument('--sqlite', default=None, help='Path to studyhub.sqlite3 (default: $STUDY_DATA_PATH/studyhub.sqlite3)')
    parser.add_argument('--chroma', default=os.getenv('CHROMA_URL', 'http://chromadb:8000'))
    parser.add_argument('--dry-run', action='store_true', help='Run every step and check, then roll back')
    args = parser.parse_args()
    settings = Settings()
    owner = os.getenv('DATABASE_OWNER_URL')
    if not owner:
        raise SystemExit('Set DATABASE_OWNER_URL (the schema owner) to run the migration.')
    sqlite_path = Path(args.sqlite) if args.sqlite else settings.data/'studyhub.sqlite3'
    try:
        report = migrate(args.email, sqlite_path=sqlite_path, owner_url=owner, chroma_url=args.chroma,
                         settings=settings, dry_run=args.dry_run)
        report['result'] = 'committed'
        handover(settings.objects)
    except _DryRun as dry:
        report = {**dry.args[0], 'result': 'dry run: every check passed; nothing was committed'}
    except (MigrationError, psycopg.Error, httpx.HTTPError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'result': 'rolled back', 'error': str(exc)}, indent=2))
        sys.exit(1)
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    main()
