"""StudyHub API: accounts, workspaces, library, reading, research and study spaces."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import logging
import os
from time import perf_counter
from typing import Literal

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import jobs, library
from .auth import require_user, router as auth_router
from .chat import ChatRequest, DOC_PREFIX, complete
from .db import Database
from .documents import router as documents_router
from .notebook import router as notebook_router
from .providers import ProviderError
from .research import router as research_router
from .runtime import HANDLERS, QUEUES, build
from .secrets import SecretsUnavailable
from .settings import Settings, env_bool
from .storage import StorageError
from .tutors import choice_for, router as tutors_router
from .workspace import router as workspace_router

logger = logging.getLogger('uvicorn.error')
UNSAFE = {'POST', 'PUT', 'PATCH', 'DELETE'}
CSRF_HEADER = 'x-studyhub-csrf'


class ServiceStatus(BaseModel):
    name: str
    url: str
    status: Literal['up', 'down']
    latency_ms: float
    http_status: int | None = None
    error: str | None = None


async def probe(client, name, url):
    start = perf_counter()
    code, error = None, None
    try:
        result = await client.get(url, timeout=4)
        code = result.status_code
        result.raise_for_status()
    except httpx.HTTPStatusError:
        error = f'Health endpoint returned HTTP {code}'
    except httpx.RequestError as exc:
        error = f'{type(exc).__name__}: connection failed or timed out'
    return ServiceStatus(name=name, url=url, status='down' if error else 'up',
                         latency_ms=round((perf_counter()-start)*1000, 1), http_status=code, error=error)


def create_app(settings=None, *, start_worker=None):
    settings = settings or Settings()
    embedded_worker = env_bool('STUDYHUB_EMBEDDED_WORKER', False) if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings.database_url, max_size=int(os.getenv('STUDYHUB_DB_POOL_SIZE', '10')))
        await db.open()
        worker_db, loops = None, []
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                app.state.db, app.state.client = db, client
                app.state.runtime = build(settings, client, db)
                if embedded_worker:
                    # Small installs can run jobs inside the API; larger ones run app.worker separately.
                    worker_db = Database(settings.database_worker_url or settings.database_url, max_size=4, name='embedded-worker')
                    await worker_db.open()
                    worker_runtime = build(settings, client, worker_db)
                    loops = [asyncio.create_task(jobs.work_loop(worker_runtime, worker_db, [q], HANDLERS)) for q in QUEUES]
                yield
        finally:
            for task in loops:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if worker_db:
                await worker_db.close()
            await db.close()

    app = FastAPI(title='StudyHub', version='0.3.0', lifespan=lifespan)
    targets = {'docling': settings.docling+'/health', 'searxng': settings.searxng+'/healthz', 'ollama': settings.ollama+'/api/tags'}

    @app.middleware('http')
    async def csrf(request: Request, call_next):
        # Cookies ride along on cross-site requests; a custom header cannot be added by
        # another site without a CORS preflight, which this API never grants.
        if request.method in UNSAFE and request.url.path.startswith(('/api/', '/v1/')) \
                and request.headers.get(CSRF_HEADER) != '1':
            return JSONResponse(status_code=403, content={'detail': 'This request is missing its security header. Reload the page and try again.'})
        return await call_next(request)

    for router in (auth_router, documents_router, workspace_router, notebook_router, tutors_router, research_router):
        app.include_router(router)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=502, content={'detail': str(exc)})

    @app.exception_handler(httpx.HTTPError)
    async def connection_error(request, exc):
        return JSONResponse(status_code=502, content={'detail': 'A dependency could not be reached. Check the status page and retry.'})

    @app.exception_handler(StorageError)
    async def storage_error(request, exc):
        logger.error('storage error: %s', exc)
        return JSONResponse(status_code=500, content={'detail': 'A stored file is missing. The administrator has been notified in the logs.'})

    @app.exception_handler(SecretsUnavailable)
    async def secrets_error(request, exc):
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    @app.get('/health')
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.db.tx() as conn:
            await conn.execute('SELECT 1')
        return {'status': 'ok', 'service': 'study-api'}

    @app.get('/status')
    async def status(request: Request, response: Response):
        principal = await require_user(request)
        services = await asyncio.gather(*(probe(request.app.state.client, name, url) for name, url in targets.items()))
        services.insert(0, ServiceStatus(name='study-api', url='/health', status='up', latency_ms=0, http_status=200))
        ready = all(s.status == 'up' for s in services)
        response.status_code = 200 if ready else 503
        runtime = request.app.state.runtime
        return {'status': 'ok' if ready else 'degraded', 'checked_at': datetime.now(timezone.utc).isoformat(),
                'services': [s.model_dump() for s in services], 'hosted_llm': settings.llm_status(),
                'embedding_model': settings.embedding_model,
                'tutor': await runtime.catalog.status(False, await choice_for(runtime, principal.ctx))}

    @app.get('/api/config')
    async def config(request: Request):
        principal = await require_user(request)
        runtime = request.app.state.runtime
        return {'hosted_llm': settings.llm_status(), 'embedding_model': settings.embedding_model,
                'tutor': await runtime.catalog.status(False, await choice_for(runtime, principal.ctx))}

    @app.post('/api/models/refresh')
    async def refresh_models(request: Request):
        principal = await require_user(request)
        runtime = request.app.state.runtime
        return await runtime.catalog.status(True, await choice_for(runtime, principal.ctx))

    # OpenAI-compatible gateway, now behind the same sign-in as everything else.
    @app.get('/v1/models')
    async def models(request: Request):
        principal = await require_user(request)
        runtime = request.app.state.runtime
        choice = await choice_for(runtime, principal.ctx)
        state = await runtime.catalog.status(False, choice)
        label = state['selected']['name'] if state['selected'] else 'install a chat model'

        def entry(model_id, name):
            return {'id': model_id, 'object': 'model', 'created': 0, 'owned_by': 'studyhub', 'name': name}
        result = [entry('studyhub-auto', 'Auto · '+label)]
        result.extend(entry(m['id'], m['name']) for m in state['models'])
        async with runtime.db.tx(principal.ctx) as conn:
            profile = await runtime.profiles.active_id(conn)
            for doc in map(library.document, await library.list_items(conn, profile)):
                if doc['status'] == 'ready':
                    result.append(entry(DOC_PREFIX+doc['id'], 'Study · '+doc['title']+' · Auto'))
        return {'object': 'list', 'data': result}

    @app.post('/v1/chat/completions')
    async def chat(body: ChatRequest, request: Request):
        principal = await require_user(request)
        runtime = request.app.state.runtime
        return await complete(runtime, body, principal.ctx, None, await choice_for(runtime, principal.ctx))

    app.state.targets = targets
    return app


app = create_app()
