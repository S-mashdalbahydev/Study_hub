"""Test harness: a fresh, migrated PostgreSQL database per test class.

Point TEST_DATABASE_OWNER_URL at any pgvector-enabled PostgreSQL 17 server where the user
may create databases, for example:

    docker run -d --name studyhub-pg-dev -e POSTGRES_USER=studyhub_owner \\
        -e POSTGRES_PASSWORD=devowner -e POSTGRES_DB=studyhub -p 127.0.0.1:55432:5432 pgvector/pgvector:pg17
"""
import hashlib
import math
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
from alembic import command
from alembic.config import Config

from app import jobs
from app.db import Database
from app.main import create_app
from app.runtime import HANDLERS, build
from app.settings import Settings

OWNER_URL = os.getenv('TEST_DATABASE_OWNER_URL', 'postgresql://studyhub_owner:devowner@127.0.0.1:55432/studyhub')
ROLE_PASSWORD = 'studyhub-test'
ROOT = Path(__file__).resolve().parents[1]


def with_database(url, database, user=None, password=None):
    parts = urlsplit(url)
    netloc = parts.netloc
    if user:
        host = netloc.rsplit('@', 1)[-1]
        netloc = f'{user}:{password}@{host}'
    return urlunsplit((parts.scheme, netloc, '/'+database, parts.query, parts.fragment))


def create_database():
    name = 'studyhub_test_'+uuid4().hex[:12]
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE {name}')
    owner = with_database(OWNER_URL, name)
    config = Config(str(ROOT/'alembic.ini'))
    config.set_main_option('script_location', str(ROOT/'migrations'))
    config.cmd_opts = type('Opts', (), {'x': [f'url={owner}']})()
    command.upgrade(config, 'head')
    with psycopg.connect(owner, autocommit=True) as conn:
        for role in ('studyhub_app', 'studyhub_worker'):
            conn.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}'")
    return name, owner, with_database(OWNER_URL, name, 'studyhub_app', ROLE_PASSWORD), \
        with_database(OWNER_URL, name, 'studyhub_worker', ROLE_PASSWORD)


def drop_database(name):
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


class FakeEmbeddings:
    """Deterministic bag-of-words vectors, so retrieval is meaningful without a model."""
    dims = 16

    async def identity(self):
        return '{"provider": "fake", "model": "fake-embed", "digest": "d1", "chunk_chars": 1400}'

    def preprocessing(self):
        return 'fake-v1'

    async def profile_key(self):
        return 'fake', 'fake-embed', 'd1', 'fake-v1'

    async def embed(self, texts, *, query=False, title=''):
        vectors = []
        for text in texts:
            vector = [0.0]*self.dims
            for word in text.lower().split():
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims] += 1
            norm = math.sqrt(sum(v*v for v in vector)) or 1
            vectors.append([v/norm for v in vector])
        return vectors


class DatabaseCase(unittest.IsolatedAsyncioTestCase):
    """Starts the API against a private database. Subclasses get `self.app`, `self.client()`."""

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.owner_url, cls.app_url, cls.worker_url = create_database()

    @classmethod
    def tearDownClass(cls):
        drop_database(cls.db_name)

    async def asyncSetUp(self):
        from app.auth import attempts
        attempts.hits.clear()  # the limiter is per-process; tests sign up many accounts
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(data=root/'data', library=root/'library', objects=root/'objects',
                                 database_url=self.app_url, database_worker_url=self.worker_url,
                                 secret_key='MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
        self.app = create_app(self.settings, start_worker=False)
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.addAsyncCleanup(self.life.__aexit__, None, None, None)
        self.runtime = self.app.state.runtime
        self.fake = FakeEmbeddings()
        self.runtime.embeddings = self.fake
        self.runtime.profiles.embeddings = self.fake
        self.worker_db = Database(self.worker_url, max_size=3, name='test-worker')
        await self.worker_db.open()
        self.addAsyncCleanup(self.worker_db.close)
        self.worker = build(self.settings, self.app.state.client, self.worker_db)
        self.worker.embeddings = self.fake
        self.worker.profiles.embeddings = self.fake
        with psycopg.connect(self.owner_url, autocommit=True) as conn:
            # Each test starts from an empty tenant set; the schema stays.
            conn.execute('TRUNCATE users, workspaces, embedding_profiles, jobs, audit_events, usage_events, '
                         'deletion_operations RESTART IDENTITY CASCADE')

    def client(self):
        api = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test',
                                headers={'X-StudyHub-CSRF': '1'})
        self.addAsyncCleanup(api.aclose)
        return api

    async def signup(self, email='reader@example.com', name='Reader', password='correct horse battery'):
        api = self.client()
        response = await api.post('/api/auth/signup', json={'display_name': name, 'email': email, 'password': password})
        self.assertEqual(response.status_code, 201, response.text)
        api.me = response.json()
        return api

    async def upload(self, api, name, content: bytes):
        response = await api.post('/api/documents/upload', files={'file': (name, content)})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['document_ids'][0]

    async def drain(self, limit=20):
        """Run queued jobs to completion with the worker role, like a real worker would."""
        for _ in range(limit):
            job = await jobs.claim(self.worker_db, ('ingest', 'research'))
            if not job:
                return
            await jobs.run_one(self.worker, self.worker_db, job, HANDLERS)
        raise AssertionError('jobs did not drain')

    def owner(self):
        return psycopg.connect(self.owner_url, autocommit=True)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def seed_book(case, api, title, *, kind='pdf', outline=(), chunks=(), content=None, ready=True, page_count=40):
    """Insert a processed book directly (as the schema owner), bypassing parsing.

    Returns ids a test may need. Structure, sections, chunks and vectors are real rows."""
    import hashlib as _hashlib
    from uuid import UUID, uuid4 as _uuid4
    from psycopg.types.json import Jsonb
    from app.db import vector_literal
    from app.structure import section_rows
    ws, user = UUID(api.me['workspace']['id']), UUID(api.me['user']['id'])
    content = content if content is not None else b'%PDF-1.4\n%%EOF\n'+title.encode()
    sha = _hashlib.sha256(content).digest()
    key = f'blobs/{ws.hex}/{sha.hex()}/1.{kind}'
    case.runtime.storage.put_bytes(key, content)
    revision = _uuid4()
    structure_key = f'revisions/{revision.hex}/structure.json'
    case.runtime.storage.put_json(structure_key, {'version': 4, 'page_count': page_count, 'outline': list(outline),
                                                  'figures': [], 'cover': False})
    vectors = await case.fake.embed([c['text'] for c in chunks]) if chunks else []
    with case.owner() as conn:
        blob = conn.execute('''INSERT INTO content_blobs (workspace_id, sha256, object_key, size_bytes, mime_type, kind)
                               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id''',
                            (ws, sha, key, len(content), 'application/pdf' if kind == 'pdf' else 'text/markdown', kind)).fetchone()[0]
        conn.execute('''INSERT INTO processing_revisions (id, workspace_id, blob_id, parser, parser_version, config_fingerprint,
                                                          state, ready_at, page_count, artifact_manifest)
                        VALUES (%s, %s, %s, 'legacy', 'test', 'test', %s, %s, %s, %s)''',
                     (revision, ws, blob, 'ready' if ready else 'pending', _now() if ready else None,
                      page_count, Jsonb({'structure_key': structure_key})))
        sections = {}
        for row in section_rows(list(outline)):
            sections[row['slug']] = conn.execute('''INSERT INTO sections (workspace_id, revision_id, parent_id, slug, title, level, position, page_start)
                                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                                                 (ws, revision, sections.get(row['parent_slug']), row['slug'], row['title'],
                                                  row['level'], row['position'], row['page_start'])).fetchone()[0]
        chunk_ids = [conn.execute('INSERT INTO chunks (workspace_id, revision_id, ordinal, page, text) VALUES (%s, %s, %s, %s, %s) RETURNING id',
                                  (ws, revision, i, c.get('page'), c['text'])).fetchone()[0] for i, c in enumerate(chunks)]
        if ready:
            conn.execute("INSERT INTO embedding_profiles (provider, model, model_digest, dimensions, preprocessing_fingerprint) VALUES ('fake','fake-embed','d1',16,'fake-v1') ON CONFLICT DO NOTHING")
            profile = conn.execute("SELECT id FROM embedding_profiles WHERE provider='fake' AND model_digest='d1'").fetchone()[0]
            vector_set = conn.execute('''INSERT INTO embedding_sets (workspace_id, revision_id, profile_id, state, expected_count, embedded_count, ready_at)
                                         VALUES (%s, %s, %s, 'ready', %s, %s, now()) RETURNING id''',
                                      (ws, revision, profile, len(chunks), len(chunks))).fetchone()[0]
            for chunk_id, vector in zip(chunk_ids, vectors):
                conn.execute('INSERT INTO embeddings (set_id, chunk_id, workspace_id, revision_id, profile_id, embedding) VALUES (%s, %s, %s, %s, %s, %s::vector)',
                             (vector_set, chunk_id, ws, revision, profile, vector_literal(vector)))
        item = conn.execute('''INSERT INTO library_items (workspace_id, created_by, blob_id, current_revision_id, title, original_filename, origin)
                               VALUES (%s, %s, %s, %s, %s, %s, 'upload') RETURNING id''',
                            (ws, user, blob, revision if ready else None, title, f'{title}.{kind}')).fetchone()[0]
    return {'id': item.hex, 'item': item, 'blob': blob, 'revision': revision, 'chunks': chunk_ids}
