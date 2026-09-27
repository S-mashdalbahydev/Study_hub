"""HTTP boundaries: local embeddings and remote tutor are separate providers."""
import hashlib
import json
import httpx


class ProviderError(RuntimeError):
    pass


async def checked(response, service):
    if response.is_error:
        # Provider bodies/URLs may echo credentials or private source text.
        raise ProviderError(f'{service} returned HTTP {response.status_code}. Check configuration and service logs.')
    return response.json()


class OllamaEmbeddings:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    async def identity(self):
        if self.settings.embedding_provider != 'ollama':
            raise ProviderError('This checkpoint supports EMBEDDING_PROVIDER=ollama. Other providers can use the embedding adapter interface.')
        data = await checked(await self.client.get(self.settings.ollama+'/api/tags'), 'Ollama')
        requested = self.settings.embedding_model
        names = {requested, requested+':latest'}
        model = next((m for m in data.get('models',[]) if m.get('name') in names or m.get('model') in names), None)
        if not model:
            raise ProviderError(f'Embedding model is not pulled. Run: docker compose exec ollama ollama pull {requested}')
        return json.dumps({'provider':'ollama','model':requested,'digest':model['digest'],
                           'chunk_chars':self.settings.chunk_chars}, sort_keys=True)

    def preprocessing(self):
        """Names the prompt template applied before embedding. Part of a profile's identity."""
        model = self.settings.embedding_model
        if model.startswith('embeddinggemma'):
            return 'embeddinggemma-task-prompts-v1'
        if model.startswith('nomic-embed-text'):
            return 'nomic-search-prefix-v1'
        return 'plain-v1'

    async def profile_key(self):
        """(provider, model, weights digest, preprocessing): one reproducible vector space."""
        identity = json.loads(await self.identity())
        return identity['provider'], identity['model'], identity['digest'], self.preprocessing()

    async def embed(self, texts, *, query=False, title=''):
        model = self.settings.embedding_model
        if model.startswith('embeddinggemma'):
            texts = [('task: retrieval | query: '+t) if query else ('title: '+title+' | text: '+t) for t in texts]
        elif model.startswith('nomic-embed-text'):
            texts = [('search_query: ' if query else 'search_document: ')+t for t in texts]
        result = await checked(await self.client.post(self.settings.ollama+'/api/embed',
            json={'model':model,'input':texts,'truncate':False}, timeout=180), 'Ollama embeddings')
        vectors = result.get('embeddings', [])
        if len(vectors) != len(texts) or any(not v for v in vectors):
            raise ProviderError('Ollama returned incomplete embeddings.')
        return vectors


class HostedTutor:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    async def open(self, messages, stream, model=None):
        # A model chosen in the UI carries its own endpoint; otherwise fall back to .env.
        url = (model.url if model and model.url else self.settings.llm_url)
        key = (model.key if model and model.key else self.settings.llm_key)
        name = (model.model if model else self.settings.llm_model)
        if not (url and name):
            raise ProviderError('This tutor connection is missing an address or a model. Open Tutor settings and check it.')
        request = self.client.build_request('POST', url+'/chat/completions',
            headers={'Authorization':'Bearer '+key} if key else {},
            json={'model':name,'messages':messages,'stream':stream}, timeout=120)
        response = await self.client.send(request, stream=True)
        if response.is_error:
            code = response.status_code
            await response.aclose()
            raise ProviderError(f'Hosted tutor returned HTTP {code}. Check the endpoint, key, model and provider account.')
        return response


class LocalTutor:
    def __init__(self, client):
        self.client = client

    async def open(self, model, messages, stream, max_tokens=768):
        # Reasoning models spend tokens thinking aloud; planning calls need more headroom
        # or the answer is cut off before it is ever written.
        payload = {'model':model.model,'messages':messages,'stream':stream,
                   'options':{'num_ctx':8192,'num_predict':max_tokens,'temperature':0.2}}
        if model.thinking:
            payload['think'] = False
        request = self.client.build_request('POST',model.url+'/api/chat',json=payload,timeout=600)
        response = await self.client.send(request,stream=True)
        if response.is_error:
            code = response.status_code
            await response.aclose()
            raise ProviderError(f'Local model returned HTTP {code}. Check Ollama logs and available memory, or choose another model.')
        return response
