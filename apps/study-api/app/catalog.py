"""Discover chat models from configured Ollama servers, without generating text."""
import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass
import httpx
from .providers import ProviderError, checked


@dataclass(frozen=True)
class TutorModel:
    id: str
    name: str
    provider: str
    model: str
    url: str = ''
    thinking: bool = False
    key: str = ''

    def public(self):
        return {'id':self.id,'name':self.name,'provider':self.provider,'model':self.model}


@dataclass(frozen=True)
class TutorChoice:
    """What one request may use: providers visible to its workspace and the user's pick."""
    providers: tuple = ()
    preferred: dict | None = None


class TutorCatalog:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings
        self._capabilities = {}
        self._cache = {}
        self._warnings = []
        self._lock = asyncio.Lock()

    async def discover(self, force=False, choice=None):
        saved = list((choice or TutorChoice()).providers)
        cache_key = tuple(sorted((str(r['id']), r['base_url'], r['model'], bool(r.get('api_key'))) for r in saved))
        async with self._lock:
            cached = self._cache.get(cache_key)
            if not force and cached and time.monotonic() < cached[0]:
                self._warnings = cached[2]
                return cached[1]
            models, warnings = [], []
            urls = [u.strip().rstrip('/') for u in
                    (self.settings.ollama_chat_urls or self.settings.ollama).split(';') if u.strip()]
            urls += [row['base_url'] for row in saved if row['kind'] == 'ollama']
            urls = list(dict.fromkeys(urls))
            async def server(url, index):
                found = []
                try:
                    data = await checked(await self.client.get(url+'/api/tags', timeout=4), 'Ollama discovery')
                    for item in data.get('models',[]):
                        name = item.get('name') or item.get('model')
                        if not name:
                            continue
                        key = (url,name,item.get('digest'))
                        if key not in self._capabilities:
                            info = await checked(await self.client.post(url+'/api/show',json={'model':name},timeout=10), 'Ollama model capabilities')
                            # Cloud-only models are not local inference, even when listed by Ollama.
                            self._capabilities[key] = (info.get('capabilities',[]), bool(info.get('remote_host') or info.get('remote_model')))
                        capabilities, remote = self._capabilities[key]
                        if 'completion' not in capabilities or remote:
                            continue
                        token = base64.urlsafe_b64encode(name.encode()).decode().rstrip('=')
                        model_id = 'studyhub-local-'+hashlib.sha256(url.encode()).hexdigest()[:12]+'-'+token
                        label = f'Local · {name}'+(f' · server {index+1}' if len(urls)>1 else '')
                        found.append(TutorModel(model_id,label,'ollama',name,url,'thinking' in capabilities))
                except (httpx.HTTPError,ProviderError,ValueError,KeyError):
                    warnings.append(f'Ollama chat server {index+1} could not be fully inspected. Check its URL and availability.')
                return found
            for found in await asyncio.gather(*(server(url,i) for i,url in enumerate(urls))):
                models.extend(found)
            models.sort(key=lambda m:(m.model,m.id))
            if self.settings.llm_status()['configured']:
                models.append(TutorModel('studyhub-hosted','Hosted · '+self.settings.llm_model,'hosted',
                                         self.settings.llm_model,self.settings.llm_url,False,self.settings.llm_key))
            for row in saved:
                if row['kind'] != 'openai' or not row['model']:
                    continue
                models.append(TutorModel('studyhub-api-'+str(row['id']),row['label']+' · '+row['model'],
                                         'hosted',row['model'],row['base_url'],False,row.get('api_key') or ''))
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[cache_key] = (time.monotonic()+3, models, warnings)
            self._warnings = warnings
            return models

    async def resolve(self, model_id='studyhub-auto', choice=None):
        models = await self.discover(choice=choice)
        if model_id != 'studyhub-auto':
            match = next((m for m in models if m.id==model_id),None)
            if match:
                return match
            if model_id=='studyhub-hosted':
                raise ProviderError('Hosted tutor is not configured. Choose a Local model or Auto, or configure the three LLM values in .env.')
            raise ProviderError('This chat model is no longer available. Refresh models and select an installed chat model.')
        chosen = (choice or TutorChoice()).preferred
        if chosen:
            wanted = ('studyhub-api-'+str(chosen['id'])) if chosen['kind']=='openai' else None
            match = next((m for m in models if m.id==wanted), None) if wanted else next(
                (m for m in models if m.provider=='ollama' and m.url==chosen['base_url']
                 and (not chosen['model'] or m.model in (chosen['model'],chosen['model']+':latest'))), None)
            if match:
                return match
        mode = self.settings.tutor_provider
        if mode not in ('auto','ollama','hosted'):
            raise ProviderError('TUTOR_PROVIDER must be auto, ollama or hosted.')
        if mode in ('auto','hosted'):
            hosted = next((m for m in models if m.provider=='hosted'),None)
            if hosted:
                return hosted
            if mode=='hosted':
                raise ProviderError('Hosted tutor is not configured. Set TUTOR_PROVIDER=auto or ollama to use local chat.')
        local = [m for m in models if m.provider=='ollama']
        preferred = self.settings.ollama_chat_model
        if preferred:
            match = next((m for m in local if m.model in (preferred,preferred+':latest')),None)
            if match:
                return match
            raise ProviderError('The preferred local chat model is not installed. Pull OLLAMA_CHAT_MODEL or clear it to select automatically.')
        if local:
            return local[0]
        raise ProviderError('No local chat model is installed. Run docker compose exec ollama ollama pull qwen3.5:0.8b, then Refresh models. Embedding models cannot answer chat questions.')

    async def status(self, force=False, choice=None):
        models = await self.discover(force, choice)
        try:
            selected = (await self.resolve('studyhub-auto', choice)).public()
            error = None
        except ProviderError as exc:
            selected, error = None, str(exc)
        return {'available':bool(selected),'selected':selected,'models':[m.public() for m in models],
                'warnings':self._warnings,'error':error}
