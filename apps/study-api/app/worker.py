"""Background worker: `python -m app.worker --queues ingest,research --concurrency 1`.

Runs as the studyhub_worker database role, the only role that can see every workspace's
queue. Start as many copies as the load needs; leases and fencing keep them from colliding.
"""
import argparse
import asyncio
import logging
import signal

import httpx

from . import jobs
from .db import Database
from .runtime import HANDLERS, QUEUES, build
from .settings import Settings

log = logging.getLogger('uvicorn.error')


async def run(queues, concurrency, settings=None):
    settings = settings or Settings()
    db = Database(settings.database_worker_url or settings.database_url, max_size=max(2, concurrency*len(queues)+1),
                  name='studyhub-worker')
    await db.open()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            runtime = build(settings, client, db)
            loops = [asyncio.create_task(jobs.work_loop(runtime, db, [queue], HANDLERS, stop=stop))
                     for queue in queues for _ in range(concurrency)]
            log.info('worker started queues=%s concurrency=%s', ','.join(queues), concurrency)
            await stop.wait()
            # Graceful: finish nothing new; in-flight jobs lose their lease and are retried.
            for task in loops:
                task.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description='StudyHub background worker')
    parser.add_argument('--queues', default=','.join(QUEUES))
    parser.add_argument('--concurrency', type=int, default=1)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    queues = [q.strip() for q in args.queues.split(',') if q.strip() in QUEUES]
    if not queues:
        raise SystemExit(f'Choose queues from: {", ".join(QUEUES)}')
    asyncio.run(run(queues, max(1, args.concurrency)))


if __name__ == '__main__':
    main()
