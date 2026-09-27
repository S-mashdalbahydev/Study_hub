"""Live Docling/Ollama/Chroma smoke test in isolated temporary state.
Run with the API virtualenv from the repo root. No hosted calls unless --hosted.
"""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/study-api'))
import httpx
from app.catalog import TutorCatalog
from app.settings import Settings
from app.store import Store
from app.ingestion import Ingestion
from app.providers import OllamaEmbeddings, ChromaVectors, HostedTutor, LocalTutor
from app.chat import ChatRequest, Message, DOC_PREFIX, complete, document_context


async def run(hosted):
    # Capture configuration in memory: never print keys or the full Compose configuration.
    compose=json.loads(subprocess.check_output(['docker','compose','config','--format','json'],cwd=ROOT))
    env=compose['services']['study-api']['environment']
    def port(service):return compose['services'][service]['ports'][0]['published']
    with tempfile.TemporaryDirectory(prefix='studyhub-smoke-') as directory:
        root=Path(directory)
        settings=Settings(data=root/'data',library=root/'library',
            chroma='http://localhost:'+str(port('chromadb')),docling='http://localhost:'+str(port('docling')),
            ollama='http://localhost:'+str(port('ollama')),embedding_model=env['EMBEDDING_MODEL'],
            embedding_provider=env['EMBEDDING_PROVIDER'],llm_url=env.get('LLM_BASE_URL',''),
            llm_key=env.get('LLM_API_KEY',''),llm_model=env.get('LLM_MODEL',''))
        async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
            store=Store(settings.data)
            embeddings=OllamaEmbeddings(client,settings)
            vectors=ChromaVectors(client,settings,namespace='studyhub-test-'+uuid4().hex[:8])
            ingestion=Ingestion(settings,store,client,embeddings,vectors)
            runtime=SimpleNamespace(settings=settings,store=store,embeddings=embeddings,vectors=vectors,tutor=HostedTutor(client,settings),local_tutor=LocalTutor(client),catalog=TutorCatalog(client,settings))
            try:
                for name in ['helios-workshop.pdf','river-garden.pdf']:
                    shutil.copyfile(ROOT/'examples'/name,settings.library/name)
                await ingestion.scan()
                docs=store.list()
                for doc in docs:
                    print('Processing',doc['filename'],flush=True)
                    await ingestion.process(doc)
                docs=store.list()
                assert len(docs)==2 and all(d['status']=='ready' for d in docs),docs
                assert all(any(c['page'] for c in store.chunks(d['id'])) for d in docs)
                print('PASS: two real PDFs -> Docling -> Ollama embeddings -> Chroma with page provenance',flush=True)
                await ingestion.scan()
                assert len(store.list())==2 and all(d['status']=='ready' for d in store.list())
                store.recover()
                assert all(d['status']=='ready' for d in store.list())
                print('PASS: duplicate refresh and restart recovery preserve ready documents',flush=True)
                for doc in docs:
                    garden=doc['filename']=='river-garden.pdf'
                    question='What color is the moisture marker and how many liters does each bed receive?' if garden else 'What color is the reference tile and how long must the operator wait?'
                    messages=[Message(role='assistant',content='OTHER_DOCUMENT_SECRET: violet submarine'),Message(role='user',content=question)]
                    context,links=await document_context(runtime,doc['id'],messages)
                    encoded=json.dumps(context)
                    assert 'OTHER_DOCUMENT_SECRET' not in encoded
                    assert ('amber' if garden else 'cobalt') in encoded.lower()
                    assert ('cobalt' if garden else 'amber') not in encoded.lower()
                    assert '#page=' in links
                    print('PASS: scoped retrieval:',doc['filename'],flush=True)
                    if hosted:
                        response=await complete(runtime,ChatRequest(model=DOC_PREFIX+doc['id']+'--studyhub-hosted',messages=messages,stream=False))
                        data=json.loads(response.body)
                        answer=data['choices'][0]['message']['content']
                        assert ('amber' if garden else 'cobalt') in answer.lower(), 'Hosted answer did not contain the expected source fact'
                        print('PASS: hosted answer:',doc['filename'],answer[:450],flush=True)
                (settings.library/'broken.pdf').write_bytes(b'%PDF-1.4\nthis is deliberately malformed')
                (settings.library/'after-failure.txt').write_text('A healthy follow-up document about a violet notebook.')
                await ingestion.scan()
                worker=asyncio.create_task(ingestion.run())
                try:
                    for _ in range(120):
                        states={d['filename']:d['status'] for d in store.list()}
                        if states.get('broken.pdf')=='failed' and states.get('after-failure.txt')=='ready':break
                        await asyncio.sleep(.5)
                    assert states.get('broken.pdf')=='failed',states
                    assert states.get('after-failure.txt')=='ready',states
                    print('PASS: malformed PDF fails visibly; worker still indexes the next document',flush=True)
                finally:
                    worker.cancel()
                    try:await worker
                    except asyncio.CancelledError:pass
            finally:
                for doc in store.list():
                    if doc['collection_id']:
                        await vectors.delete_collection(doc['collection_id'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--hosted',action='store_true',help='Make two real hosted tutoring calls (may incur provider charges).')
    asyncio.run(run(parser.parse_args().hosted))
