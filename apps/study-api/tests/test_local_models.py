import json
import unittest
from types import SimpleNamespace
import httpx
from app.catalog import TutorCatalog
from app.settings import Settings
from app.providers import LocalTutor, HostedTutor
from app.chat import complete, ChatRequest, Message

class LocalModelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.names=['embed','chat','cloud']; self.mode='normal'; self.calls=[]
        def handler(r):
            body=json.loads(r.content) if r.content else {}; self.calls.append((str(r.url),body))
            if r.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':n,'digest':n} for n in self.names]})
            if r.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['embedding'] if body['model']=='embed' else ['completion','thinking'],'remote_host':'cloud' if body['model']=='cloud' else ''})
            if r.url.path=='/api/chat':
                self.assertNotIn('authorization',r.headers);self.assertFalse(body['think'])
                if self.mode=='error':return httpx.Response(503)
                if not body['stream'] or self.mode=='json':return httpx.Response(200,json={'message':{'content':'Local answer'},'done':True})
                content=json.dumps({'message':{'content':'Local answer'},'done':self.mode!='truncated'})+'\n'
                return httpx.Response(200,text=content,headers={'content-type':'application/x-ndjson'})
            raise AssertionError(str(r.url))
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(handler));self.addAsyncCleanup(self.client.aclose)
        self.settings=Settings(llm_key='',llm_model='',llm_url='',ollama_chat_urls='http://one;http://two')
        self.catalog=TutorCatalog(self.client,self.settings)
        self.runtime=SimpleNamespace(catalog=self.catalog,local_tutor=LocalTutor(self.client),tutor=HostedTutor(self.client,self.settings))
    async def test_discovery_multiple_servers_add_remove_and_filter(self):
        first=await self.catalog.discover();self.assertEqual(len(first),2);self.assertNotEqual(first[0].id,first[1].id)
        self.names.append('new');self.assertEqual(len(await self.catalog.discover(force=True)),4)
        self.names.remove('chat');remaining=await self.catalog.discover(force=True);self.assertEqual(len(remaining),2);self.assertTrue(all(m.model=='new' for m in remaining))
    async def test_local_stream_nonstream_and_json_adaptation(self):
        for mode,stream in [('normal',False),('normal',True),('json',True)]:
            self.mode=mode
            r=await complete(self.runtime,ChatRequest(model='studyhub-auto',messages=[Message(role='user',content='Hi')],stream=stream),None)
            if stream:
                s=''.join([x async for x in r.body_iterator]);self.assertEqual(s.count('[DONE]'),1)
                chunks=[json.loads(x[6:]) for x in s.split('\n\n') if x.startswith('data: {')]
                self.assertEqual(len({x['id'] for x in chunks}),1)
            else:s=r.body.decode()
            self.assertIn('Local answer',s)
        self.assertTrue(any(url.endswith('/api/chat') for url,_ in self.calls))
    async def test_failures_stay_streaming(self):
        for mode,model in [('error','studyhub-auto'),('truncated','studyhub-auto'),('normal','studyhub-hosted'),('normal','missing')]:
            self.mode=mode
            r=await complete(self.runtime,ChatRequest(model=model,messages=[Message(role='user',content='Hi')],stream=True),None)
            self.assertEqual(r.status_code,200);self.assertEqual(r.media_type,'text/event-stream')
            s=''.join([x async for x in r.body_iterator]);self.assertIn('could not complete',s);self.assertEqual(s.count('[DONE]'),1)
