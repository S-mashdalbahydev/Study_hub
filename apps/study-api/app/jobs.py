"""Durable jobs in PostgreSQL with leases and fencing tokens.

Delivery is at-least-once. A worker claims a job in a short transaction and receives a
fresh lease token. Anything that publishes results runs inside `fenced()`, which locks the
job row and proceeds only while that token is still current. A worker that stalled past its
lease, was replaced, and then wakes up finds its token gone and its publication refused.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
import random
import socket
from uuid import UUID

from psycopg.types.json import Jsonb

from .db import Context, one

log = logging.getLogger('uvicorn.error')
LEASE_SECONDS = 120
WORKER_NAME = f'{socket.gethostname()}:{os.getpid()}'


class Superseded(Exception):
    """This attempt no longer owns the job; its results must not be published."""


class PermanentFailure(Exception):
    """Retrying cannot help (for example an unreadable file)."""


class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: UUID
    workspace_id: UUID
    requested_by: UUID | None
    queue: str
    kind: str
    target_type: str
    target_id: UUID
    target_generation: int
    payload: dict
    attempts: int
    max_attempts: int
    lease_token: UUID

    @property
    def ctx(self):
        return Context(self.requested_by, self.workspace_id)


async def enqueue(conn, *, workspace_id, queue, kind, target_type, target_id, requested_by=None,
                  dedupe_key=None, payload=None, priority=100, target_generation=0, max_attempts=5):
    """Insert a job in the caller's transaction. An open job with the same dedupe key is reused."""
    row = await one(conn, '''
        INSERT INTO jobs (workspace_id, requested_by, queue, kind, target_type, target_id, target_generation,
                          dedupe_key, payload, priority, max_attempts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) WHERE state IN ('queued', 'running') DO NOTHING
        RETURNING id''', (workspace_id, requested_by, queue, kind, target_type, target_id, target_generation,
                          dedupe_key, Jsonb(payload or {}), priority, max_attempts))
    if row:
        await conn.execute('SELECT pg_notify(%s, %s)', ('studyhub_jobs', queue))
        return row['id']
    existing = await one(conn, "SELECT id FROM jobs WHERE dedupe_key = %s AND state IN ('queued', 'running')", (dedupe_key,))
    return existing['id'] if existing else None


async def claim(db, queues, worker=WORKER_NAME, lease_seconds=LEASE_SECONDS):
    """Claim one runnable job: queued and due, or running with an expired lease."""
    async with db.tx() as conn:
        row = await one(conn, '''
            SELECT id, state, attempts, max_attempts FROM jobs
            WHERE queue = ANY(%s)
              AND ((state = 'queued' AND available_at <= now())
                   OR (state = 'running' AND lease_expires_at < now()))
            ORDER BY priority, available_at
            FOR UPDATE SKIP LOCKED LIMIT 1''', (list(queues),))
        if not row:
            return None
        if row['state'] == 'running':
            # The previous holder lost its lease. Whatever it does next is fenced out.
            await conn.execute('''UPDATE job_attempts SET outcome = 'superseded', finished_at = now()
                                  WHERE job_id = %s AND outcome = 'running' ''', (row['id'],))
            if row['attempts'] >= row['max_attempts']:
                await conn.execute('''UPDATE jobs SET state = 'failed', lease_token = NULL, lease_owner = NULL,
                                      error_code = 'transient', error = 'The job stopped responding too many times.',
                                      finished_at = now() WHERE id = %s''', (row['id'],))
                return None
        claimed = await one(conn, '''
            UPDATE jobs SET state = 'running', attempts = attempts + 1, lease_token = gen_random_uuid(),
                            lease_owner = %s, lease_expires_at = now() + make_interval(secs => %s)
            WHERE id = %s
            RETURNING id, workspace_id, requested_by, queue, kind, target_type, target_id, target_generation,
                      payload, attempts, max_attempts, lease_token''', (worker, lease_seconds, row['id']))
        await conn.execute('INSERT INTO job_attempts (job_id, attempt_no, lease_token, worker) VALUES (%s, %s, %s, %s)',
                           (claimed['id'], claimed['attempts'], claimed['lease_token'], worker))
    return Job(**claimed)


async def heartbeat(db, job, lease_seconds=LEASE_SECONDS):
    async with db.tx() as conn:
        row = await one(conn, '''UPDATE jobs SET lease_expires_at = now() + make_interval(secs => %s)
                                 WHERE id = %s AND lease_token = %s AND state = 'running'
                                 RETURNING cancel_requested''', (lease_seconds, job.id, job.lease_token))
    if not row:
        raise Superseded()
    if row['cancel_requested']:
        raise Cancelled()


async def set_stage(db, job, stage):
    async with db.tx() as conn:
        await conn.execute('UPDATE jobs SET stage = %s WHERE id = %s AND lease_token = %s', (stage, job.id, job.lease_token))


@asynccontextmanager
async def fenced(db, job, ctx=None):
    """A transaction that commits only while this attempt still holds the job."""
    async with db.tx(ctx or job.ctx) as conn:
        row = await one(conn, "SELECT 1 FROM jobs WHERE id = %s AND lease_token = %s AND state = 'running' FOR UPDATE",
                        (job.id, job.lease_token))
        if not row:
            raise Superseded()
        yield conn


async def succeed(db, job):
    async with fenced(db, job) as conn:
        await conn.execute('''UPDATE jobs SET state = 'succeeded', lease_token = NULL, lease_owner = NULL,
                              lease_expires_at = NULL, error = NULL, error_code = NULL, finished_at = now()
                              WHERE id = %s''', (job.id,))
        await conn.execute("UPDATE job_attempts SET outcome = 'published', finished_at = now() WHERE lease_token = %s",
                           (job.lease_token,))


async def fail(db, job, message, *, permanent=False, cancelled=False):
    retry = not permanent and not cancelled and job.attempts < job.max_attempts
    # Exponential backoff with jitter spreads retries of a failing dependency.
    delay = min(900, 2 ** job.attempts * 5) * random.uniform(0.7, 1.3) if retry else 0
    state = 'queued' if retry else ('cancelled' if cancelled else 'failed')
    code = 'cancelled' if cancelled else ('permanent' if permanent else 'transient')
    async with db.tx() as conn:
        updated = await one(conn, '''
            UPDATE jobs SET state = %s, lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL,
                            available_at = now() + make_interval(secs => %s), error = %s, error_code = %s,
                            finished_at = CASE WHEN %s THEN NULL ELSE now() END
            WHERE id = %s AND lease_token = %s RETURNING id''',
            (state, delay, message[:2000], code, retry, job.id, job.lease_token))
        if updated:
            await conn.execute("UPDATE job_attempts SET outcome = %s, error = %s, finished_at = now() WHERE lease_token = %s",
                               ('cancelled' if cancelled else 'failed', message[:2000], job.lease_token))
    return retry


async def mark_superseded(db, job):
    async with db.tx() as conn:
        await conn.execute("UPDATE job_attempts SET outcome = 'superseded', finished_at = now() WHERE lease_token = %s AND outcome = 'running'",
                           (job.lease_token,))


async def run_one(runtime, db, job, handlers):
    handler = handlers.get(job.kind)
    beat = None

    async def keep_alive():
        while True:
            await asyncio.sleep(LEASE_SECONDS/3)
            await heartbeat(db, job)

    try:
        if not handler:
            raise PermanentFailure(f'No handler for job kind {job.kind}.')
        beat = asyncio.create_task(keep_alive())
        work = asyncio.create_task(handler(runtime, db, job))
        done, _ = await asyncio.wait({work, beat}, return_when=asyncio.FIRST_COMPLETED)
        if beat in done and not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            beat.result()  # re-raise Superseded / Cancelled
        await work
        await succeed(db, job)
    except Superseded:
        log.info('job superseded id=%s kind=%s attempt=%s', job.id, job.kind, job.attempts)
        await mark_superseded(db, job)
    except Cancelled:
        await fail(db, job, 'Cancelled by the user.', cancelled=True)
    except PermanentFailure as exc:
        await fail(db, job, str(exc), permanent=True)
        await _final_failure(runtime, db, job, handlers, str(exc))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        from .providers import ProviderError
        # Never log provider bodies, credentials or document text.
        message = str(exc) if isinstance(exc, ProviderError) else f'{type(exc).__name__}: processing failed; it will be retried.'
        log.warning('job failed id=%s kind=%s attempt=%s error=%s', job.id, job.kind, job.attempts, type(exc).__name__)
        if not await fail(db, job, message):
            await _final_failure(runtime, db, job, handlers, message)
    finally:
        if beat:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)


async def _final_failure(runtime, db, job, handlers, message):
    # Lets a domain record the failure on its own rows (for example research_jobs.status).
    hook = handlers.get('on_failure:'+job.kind)
    if hook:
        try:
            await hook(runtime, db, job, message)
        except Exception as exc:
            log.warning('failure hook failed id=%s error=%s', job.id, type(exc).__name__)


async def work_loop(runtime, db, queues, handlers, *, idle=1.0, stop=None):
    """Claim and run jobs until cancelled. Several loops may run on many machines."""
    while not (stop and stop.is_set()):
        try:
            job = await claim(db, queues)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('job claim failed: %s', type(exc).__name__)
            job = None
        if job:
            await run_one(runtime, db, job, handlers)
            continue
        await asyncio.sleep(idle)
