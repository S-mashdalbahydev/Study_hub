"""The guarantees the multi-user design exists for, each tested against real PostgreSQL."""
import asyncio
import re
import uuid

import psycopg

from app import jobs, library
from app.db import Context, one
from tests.support import DatabaseCase

BOOK = b'# Caching\n\n' + b'HTTP caching stores responses so repeated requests are fast. ' * 30 + \
       b'\n\n## Validation\n\n' + b'ETags let a client revalidate a stale response cheaply. ' * 30
HEX = re.compile(r'^[0-9a-f]{32}$')


class AccountTests(DatabaseCase):
    async def test_signup_creates_personal_workspace_and_first_admin(self):
        first = await self.signup('a@example.com', 'Ada')
        second = await self.signup('b@example.com', 'Bo')
        self.assertTrue(first.me['user']['is_instance_admin'])
        self.assertFalse(second.me['user']['is_instance_admin'])
        self.assertEqual(first.me['workspace']['kind'], 'personal')
        self.assertEqual(first.me['workspace']['role'], 'owner')
        self.assertNotEqual(first.me['workspace']['id'], second.me['workspace']['id'])
        self.assertTrue(HEX.match(first.me['user']['id']))

    async def test_duplicate_email_is_rejected_case_insensitively(self):
        await self.signup('Same@Example.com')
        response = await self.client().post('/api/auth/signup', json={
            'display_name': 'Other', 'email': 'same@example.COM', 'password': 'another long password'})
        self.assertEqual(response.status_code, 409)

    async def test_login_logout_and_wrong_password(self):
        await self.signup('c@example.com', password='a very long password')
        api = self.client()
        self.assertEqual((await api.post('/api/auth/login', json={'email': 'c@example.com', 'password': 'nope'})).status_code, 401)
        self.assertEqual((await api.post('/api/auth/login', json={'email': 'c@example.com', 'password': 'a very long password'})).status_code, 200)
        self.assertEqual((await api.get('/api/auth/me')).status_code, 200)
        self.assertEqual((await api.post('/api/auth/logout')).status_code, 204)
        self.assertEqual((await api.get('/api/documents')).status_code, 401)

    async def test_requests_need_a_session_and_the_csrf_header(self):
        anonymous = self.client()
        self.assertEqual((await anonymous.get('/api/documents')).status_code, 401)
        api = await self.signup()
        response = await api.post('/api/library/refresh', headers={'X-StudyHub-CSRF': ''})
        self.assertEqual(response.status_code, 403)

    async def test_deletion_grace_then_recreation_gets_a_new_identity(self):
        api = await self.signup('d@example.com', password='first long password')
        old_id = api.me['user']['id']
        response = await api.request('DELETE', '/api/auth/account', json={'password': 'first long password'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual((await api.get('/api/auth/me')).status_code, 401)
        # During the grace period the address is still taken, and signing in cancels deletion.
        blocked = await self.client().post('/api/auth/signup', json={
            'display_name': 'D', 'email': 'd@example.com', 'password': 'second long password'})
        self.assertEqual(blocked.status_code, 409)
        back = await self.client().post('/api/auth/login', json={'email': 'd@example.com', 'password': 'first long password'})
        self.assertTrue(back.json()['deletion_cancelled'])
        # A completed purge releases the address; signing up again is a brand-new person.
        with self.owner() as conn:
            conn.execute("UPDATE auth_identities SET revoked_at = now() WHERE user_id = %s", (uuid.UUID(old_id),))
            conn.execute("UPDATE users SET status = 'deleted' WHERE id = %s", (uuid.UUID(old_id),))
        again = await self.signup('d@example.com', password='second long password')
        self.assertNotEqual(again.me['user']['id'], old_id)
        self.assertEqual((await again.get('/api/documents')).json(), [])


class IsolationTests(DatabaseCase):
    async def test_another_workspace_cannot_see_or_open_a_document(self):
        alice, bob = await self.signup('alice@example.com'), await self.signup('bob@example.com')
        doc = await self.upload(alice, 'notes.md', BOOK)
        await self.drain()
        self.assertEqual([d['id'] for d in (await alice.get('/api/documents')).json()], [doc])
        self.assertEqual((await bob.get('/api/documents')).json(), [])
        for path in (f'/api/documents/{doc}', f'/api/documents/{doc}/source', f'/api/documents/{doc}/reader'):
            self.assertEqual((await bob.get(path)).status_code, 404, path)
        self.assertEqual((await bob.post('/api/sessions', json={'document_id': doc})).status_code, 404)

    async def test_row_level_security_hides_other_tenants_even_without_a_where_clause(self):
        alice, bob = await self.signup('a2@example.com'), await self.signup('b2@example.com')
        await self.upload(alice, 'notes.md', BOOK)
        await self.drain()
        bob_ctx = Context(uuid.UUID(bob.me['user']['id']), uuid.UUID(bob.me['workspace']['id']))
        async with self.runtime.db.tx(bob_ctx) as conn:
            for table in ('library_items', 'content_blobs', 'chunks', 'embeddings', 'processing_revisions'):
                self.assertEqual((await one(conn, f'SELECT count(*) AS n FROM {table}'))['n'], 0, table)
        async with self.runtime.db.tx() as conn:  # no identity at all
            self.assertEqual((await one(conn, 'SELECT count(*) AS n FROM chunks'))['n'], 0)

    async def test_composite_keys_reject_a_cross_workspace_reference(self):
        alice, bob = await self.signup('a3@example.com'), await self.signup('b3@example.com')
        doc = await self.upload(alice, 'notes.md', BOOK)
        session = (await alice.post('/api/sessions', json={'document_id': doc})).json()
        with self.owner() as conn, self.assertRaises(psycopg.errors.ForeignKeyViolation):
            # The owner bypasses RLS, so only the composite foreign key can stop this.
            conn.execute("INSERT INTO messages (workspace_id, session_id, role, content, status) VALUES (%s, %s, 'user', 'x', 'complete')",
                         (uuid.UUID(bob.me['workspace']['id']), uuid.UUID(session['id'])))

    async def test_pooled_connections_do_not_carry_identity(self):
        api = await self.signup('pool@example.com')
        ctx = Context(uuid.UUID(api.me['user']['id']), uuid.UUID(api.me['workspace']['id']))
        async with self.runtime.db.tx(ctx) as conn:
            self.assertEqual((await one(conn, "SELECT current_setting('app.workspace_id', true) AS w"))['w'], str(ctx.workspace_id))
        for _ in range(3):
            async with self.runtime.db.tx() as conn:
                self.assertIn((await one(conn, "SELECT current_setting('app.workspace_id', true) AS w"))['w'], ('', None))
        with self.assertRaises(RuntimeError):
            async with self.runtime.db.tx(ctx) as conn:
                raise RuntimeError('rolled back')
        async with self.runtime.db.tx() as conn:
            self.assertIn((await one(conn, "SELECT current_setting('app.workspace_id', true) AS w"))['w'], ('', None))


class LibraryLifecycleTests(DatabaseCase):
    async def test_upload_processes_to_ready_and_ids_are_hex(self):
        api = await self.signup()
        doc = await self.upload(api, 'notes.md', BOOK)
        self.assertTrue(HEX.match(doc))
        self.assertEqual((await api.get(f'/api/documents/{doc}')).json()['status'], 'discovered')
        await self.drain()
        ready = (await api.get(f'/api/documents/{doc}')).json()
        self.assertEqual(ready['status'], 'ready')
        self.assertGreater(ready['chunk_count'], 0)
        # Hyphenated input is accepted too.
        self.assertEqual((await api.get(f'/api/documents/{uuid.UUID(doc)}')).json()['id'], doc)
        reader = (await api.get(f'/api/documents/{doc}/reader')).json()
        self.assertIn('HTTP caching', reader['content'])

    async def test_same_bytes_attach_once_even_concurrently(self):
        api = await self.signup()
        first, second = await asyncio.gather(self.upload(api, 'a.md', BOOK), self.upload(api, 'b.md', BOOK))
        self.assertEqual(first, second)
        again = await api.post('/api/documents/upload', files={'file': ('c.md', BOOK)})
        self.assertEqual(again.json()['document_ids'], [first])
        self.assertFalse(again.json()['created'])
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM content_blobs').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0], 1)

    async def test_same_bytes_in_two_workspaces_are_separate_blobs(self):
        alice, bob = await self.signup('x@example.com'), await self.signup('y@example.com')
        self.assertNotEqual(await self.upload(alice, 'a.md', BOOK), await self.upload(bob, 'a.md', BOOK))
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT count(DISTINCT workspace_id) FROM content_blobs').fetchone()[0], 2)

    async def test_delete_reupload_restore_contract(self):
        api = await self.signup()
        original = await self.upload(api, 'notes.md', BOOK)
        await self.drain()
        self.assertEqual((await api.delete(f'/api/documents/{original}')).status_code, 204)
        self.assertEqual((await api.get(f'/api/documents/{original}')).status_code, 404)
        self.assertEqual((await api.get('/api/documents')).json(), [])
        fresh = await self.upload(api, 'notes.md', BOOK)
        self.assertNotEqual(fresh, original)
        # Processing is reused: the new item is readable without another parse.
        self.assertEqual((await api.get(f'/api/documents/{fresh}')).json()['status'], 'ready')
        conflict = await api.post(f'/api/documents/{original}/restore')
        self.assertEqual(conflict.status_code, 409)
        await api.delete(f'/api/documents/{fresh}')
        restored = await api.post(f'/api/documents/{original}/restore')
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()['id'], original)

    async def test_delete_while_processing_does_not_resurrect(self):
        api = await self.signup()
        doc = await self.upload(api, 'notes.md', BOOK)
        await api.delete(f'/api/documents/{doc}')
        await self.drain()
        self.assertEqual((await api.get(f'/api/documents/{doc}')).status_code, 404)
        with self.owner() as conn:
            self.assertEqual(conn.execute("SELECT state FROM library_items WHERE id = %s", (uuid.UUID(doc),)).fetchone()[0], 'trashed')

    async def test_retrieval_stays_inside_the_document(self):
        api = await self.signup()
        doc = await self.upload(api, 'notes.md', BOOK)
        await self.upload(api, 'other.md', b'# Gardening\n\n' + b'Tomatoes need sun and water every day. ' * 40)
        await self.drain()
        ctx = Context(uuid.UUID(api.me['user']['id']), uuid.UUID(api.me['workspace']['id']))
        async with self.runtime.db.tx(ctx) as conn:
            profile = await self.runtime.profiles.active_id(conn)
            item = await library.get_item(conn, uuid.UUID(doc), profile)
            vector = (await self.fake.embed(['tomatoes sun water'], query=True))[0]
            matches = await library.search(conn, item, profile, vector, 5)
        self.assertTrue(matches)
        self.assertTrue(all('Tomatoes' not in m['text'] for m in matches))


class JobFencingTests(DatabaseCase):
    async def test_a_stale_worker_cannot_publish_after_takeover(self):
        api = await self.signup()
        await self.upload(api, 'notes.md', BOOK)
        stale = await jobs.claim(self.worker_db, ['ingest'])
        with self.owner() as conn:  # the first worker stalls past its lease
            conn.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second'")
        current = await jobs.claim(self.worker_db, ['ingest'])
        self.assertEqual(current.id, stale.id)
        self.assertNotEqual(current.lease_token, stale.lease_token)
        with self.assertRaises(jobs.Superseded):
            async with jobs.fenced(self.worker_db, stale):
                pass
        with self.assertRaises(jobs.Superseded):
            await jobs.heartbeat(self.worker_db, stale)
        await jobs.run_one(self.worker, self.worker_db, current, {**__import__('app.runtime', fromlist=['HANDLERS']).HANDLERS})
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT state FROM jobs').fetchone()[0], 'succeeded')
            outcomes = [r[0] for r in conn.execute('SELECT outcome FROM job_attempts ORDER BY attempt_no')]
        self.assertEqual(outcomes, ['superseded', 'published'])

    async def test_transient_failures_retry_with_backoff_then_fail(self):
        api = await self.signup()
        await self.upload(api, 'notes.md', BOOK)

        async def broken(runtime, db, job):
            raise RuntimeError('dependency down')
        job = await jobs.claim(self.worker_db, ['ingest'])
        await jobs.run_one(self.worker, self.worker_db, job, {'ingest': broken})
        with self.owner() as conn:
            state, available, attempts = conn.execute('SELECT state, available_at > now(), attempts FROM jobs').fetchone()
        self.assertEqual((state, available, attempts), ('queued', True, 1))


class ConstraintTests(DatabaseCase):
    async def test_embedding_dimensions_must_match_the_profile(self):
        api = await self.signup()
        await self.upload(api, 'notes.md', BOOK)
        await self.drain()
        with self.owner() as conn, self.assertRaises(psycopg.errors.CheckViolation):
            conn.execute('''UPDATE embeddings SET embedding = '[1,2,3]'::vector
                            WHERE chunk_id = (SELECT min(chunk_id) FROM embeddings)''')

    async def test_a_workspace_keeps_an_owner(self):
        api = await self.signup()
        with self.owner() as conn, self.assertRaises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE memberships SET role = 'member' WHERE workspace_id = %s",
                         (uuid.UUID(api.me['workspace']['id']),))

    async def test_a_cited_revision_cannot_be_deleted(self):
        api = await self.signup()
        doc = await self.upload(api, 'notes.md', BOOK)
        await self.drain()
        session = (await api.post('/api/sessions', json={'document_id': doc})).json()
        with self.owner() as conn:
            item = conn.execute('SELECT workspace_id, blob_id, current_revision_id FROM library_items WHERE id = %s',
                                (uuid.UUID(doc),)).fetchone()
            message = conn.execute("INSERT INTO messages (workspace_id, session_id, role, content, status) VALUES (%s, %s, 'assistant', 'a', 'complete') RETURNING id",
                                   (item[0], uuid.UUID(session['id']))).fetchone()[0]
            chunk = conn.execute('SELECT id FROM chunks WHERE revision_id = %s LIMIT 1', (item[2],)).fetchone()[0]
            conn.execute('''INSERT INTO citations (message_id, ordinal, workspace_id, library_item_id, blob_id, revision_id, chunk_id, label_snapshot)
                            VALUES (%s, 1, %s, %s, %s, %s, %s, 'p. 1')''', (message, item[0], uuid.UUID(doc), item[1], item[2], chunk))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute('DELETE FROM processing_revisions WHERE id = %s', (item[2],))
