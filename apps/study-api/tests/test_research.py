from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.providers import ProviderError
from app.research import (companion_document_markdown, companion_header, companion_preview_draft,
    companion_resource_candidates, companion_units, document_chapters, is_relevant,
    plan_research, public_url, searchable_book_title, synthesize_section, validate_article,
    validate_playlist, validate_video)
from app.storage import LocalStorage
from tests.support import DatabaseCase


def fake_response(status_code=200, json_data=None, text='', headers=None, url='https://example.com'):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {'content-type': 'text/html'}
    response.text = text
    response.content = text.encode()
    response.url = url
    response.json = MagicMock(return_value=json_data or {})
    def raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError('error', request=MagicMock(), response=response)
    response.raise_for_status = raise_for_status
    return response


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    def test_companion_uses_real_chapter_markers_and_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            storage=LocalStorage(Path(directory))
            storage.put_json('revisions/r/structure.json',{'outline':[
                {'title':'CHAPTER 1','page':10,'level':1},{'title':'Storage Engines','page':10,'level':1},
                {'title':'Details','page':12,'level':1},{'title':'CHAPTER 2 Replication','page':30,'level':1}]})
            book={'id':'book','title':'Book','revision_state':'ready','set_state':'ready',
                  'artifact_manifest':{'structure_key':'revisions/r/structure.json'}}
            _,chapters=document_chapters(SimpleNamespace(storage=storage),book,'deep')
        self.assertEqual(chapters,[{'number':1,'title':'Storage Engines','page':10},{'number':2,'title':'Replication','page':30}])

    def test_companion_refuses_a_document_that_is_not_ready(self):
        with self.assertRaises(ProviderError):
            document_chapters(SimpleNamespace(storage=None),{'revision_state':'ready','set_state':'building','job_state':'running','job_stage':'embedding'},'deep')

    def test_companion_recovers_a_truncated_library_title_from_book_text(self):
        title=searchable_book_title({'id':'book','title':'Designing Data-Intensive Applic',
                                     'chunks':[{'text':'Designing Data-Intensive Applications\nMartin Kleppmann'}]})
        self.assertEqual(title,'Designing Data-Intensive Applications')

    def test_companion_combines_long_sources_into_learning_units(self):
        chapters=[{'number':n,'title':f'Topic {n}','page':n*10} for n in range(1,7)]
        units=companion_units(chapters)
        self.assertEqual([len(x['chapters']) for x in units],[2,2,2])
        self.assertEqual(units[0]['number'],'1, 2')

    def test_private_urls_are_rejected(self):
        for url in ("http://127.0.0.1/private", "http://localhost/private", "http://[::1]/private"):
            with self.assertRaises(ProviderError):
                public_url(url)

    async def test_small_model_gets_a_subject_specific_structural_fallback(self):
        job = {"query": "HTTP caching", "depth": "quick", "level": "beginner", "goal": "apply it"}
        with patch("app.research.model_text", AsyncMock(return_value="Here is the plan, but it is not JSON.")):
            plan = await plan_research(SimpleNamespace(), job)
        self.assertEqual(plan["title"], "HTTP caching")
        self.assertEqual(plan["search_queries"][0], "HTTP caching")
        self.assertGreaterEqual(len(plan["outline"]), 3)

    async def test_synthesis_uses_real_date_and_verifiable_source_links(self):
        source = {"id": "S0123456789", "title": "Primary guide", "url": "https://example.com/guide",
                  "publisher": "example.com", "retrieved_at": "2026-09-12T08:00:00+00:00", "excerpt": "Evidence."}
        model_draft = "# Guide\n\n**Research Date:** January 1, 2020\n\n## Foundation\nA supported fact [S0123456789]."
        job = {"level": "beginner", "goal": "learn", "depth": "quick"}
        with patch("app.research.model_text", AsyncMock(return_value=model_draft)):
            result = await synthesize_section(SimpleNamespace(), {"outline": ["Foundation"]}, [source], job)
        self.assertIn(f"**Research Date:** {datetime.now(timezone.utc).date().isoformat()}", result)
        self.assertIn("[S0123456789](https://example.com/guide)", result)
        self.assertIn("## Verified source list", result)

    async def test_synthesis_removes_invented_citations_and_keeps_verified_sources(self):
        source = {"id": "S0123456789", "title": "Guide", "url": "https://example.com/guide",
                  "publisher": "example.com", "retrieved_at": "2026-09-12T08:00:00+00:00", "excerpt": "Evidence."}
        job = {"level": "beginner", "goal": "learn", "depth": "quick"}
        with patch("app.research.model_text", AsyncMock(return_value="Unsupported [Sffffffffff]")):
            result=await synthesize_section(SimpleNamespace(), {}, [source], job)
        self.assertNotIn("Sffffffffff",result)
        self.assertIn("Citation note",result)
        self.assertIn("[S0123456789] [Guide](https://example.com/guide)",result)

    async def test_synthesis_maps_numeric_citations_to_retained_sources(self):
        source = {"id": "S0123456789", "title": "Guide", "url": "https://example.com/guide",
                  "publisher": "example.com", "retrieved_at": "2026-09-12T08:00:00+00:00", "excerpt": "Evidence."}
        job = {"level": "beginner", "goal": "learn", "depth": "quick"}
        with patch("app.research.model_text", AsyncMock(return_value="Supported statement [1].")):
            result=await synthesize_section(SimpleNamespace(), {}, [source], job)
        self.assertIn("[S0123456789](https://example.com/guide)",result)
        self.assertNotIn("Citation note",result)


class RelevanceTests(unittest.TestCase):
    def test_relevance_requires_a_shared_subject_word(self):
        self.assertTrue(is_relevant("Full Kubernetes Course for Beginners", "Kubernetes Fundamentals", "The Book"))
        self.assertFalse(is_relevant("Ten Tips for Better Sleep", "Kubernetes Fundamentals", "The Book"))

    def test_relevance_rejects_a_title_with_no_significant_words(self):
        self.assertFalse(is_relevant("", "Kubernetes Fundamentals"))
        self.assertFalse(is_relevant("The And For", "Kubernetes Fundamentals"))


class ValidatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_video_accepts_a_relevant_public_video(self):
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, {'title': 'Kubernetes Storage Explained', 'author_name': 'Cloud Channel'})))
        runtime = SimpleNamespace(client=client)
        result = await validate_video(runtime, 'https://www.youtube.com/watch?v=abc123xyz', 'Storage Engines', 'Kubernetes Fundamentals')
        self.assertIsNotNone(result)
        self.assertEqual(result['publisher'], 'Cloud Channel')

    async def test_validate_video_rejects_private_or_deleted_videos(self):
        for status in (401, 404):
            client = SimpleNamespace(get=AsyncMock(return_value=fake_response(status, {})))
            runtime = SimpleNamespace(client=client)
            result = await validate_video(runtime, 'https://www.youtube.com/watch?v=abc123xyz', 'Storage Engines', 'Book')
            self.assertIsNone(result)

    async def test_validate_video_rejects_an_off_topic_result(self):
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, {'title': 'Ten Tips for Better Sleep', 'author_name': 'Wellness Channel'})))
        runtime = SimpleNamespace(client=client)
        result = await validate_video(runtime, 'https://www.youtube.com/watch?v=abc123xyz', 'Storage Engines', 'Kubernetes Fundamentals')
        self.assertIsNone(result)

    async def test_validate_video_rejects_non_youtube_or_malformed_urls(self):
        runtime = SimpleNamespace(client=SimpleNamespace(get=AsyncMock()))
        self.assertIsNone(await validate_video(runtime, 'https://evil.example.com/watch?v=abc123xyz', 'Storage', 'Book'))
        self.assertIsNone(await validate_video(runtime, 'https://www.youtube.com/watch?v=short', 'Storage', 'Book'))

    async def test_validate_playlist_accepts_a_real_relevant_playlist_page(self):
        html = '<html><head><meta property="og:title" content="Kubernetes Storage Deep Dive - YouTube"></head><body>Full course</body></html>'
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text=html, url='https://www.youtube.com/playlist?list=xyz')))
        runtime = SimpleNamespace(client=client)
        result = await validate_playlist(runtime, 'https://www.youtube.com/playlist?list=xyz', 'Storage Engines', 'Kubernetes Fundamentals')
        self.assertIsNotNone(result)
        self.assertEqual(result['title'], 'Kubernetes Storage Deep Dive')

    async def test_validate_playlist_rejects_a_deleted_or_private_playlist(self):
        html = '<html><head><title>YouTube</title></head><body>This playlist does not exist.</body></html>'
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text=html, url='https://www.youtube.com/playlist?list=xyz')))
        runtime = SimpleNamespace(client=client)
        result = await validate_playlist(runtime, 'https://www.youtube.com/playlist?list=xyz', 'Storage Engines', 'Book')
        self.assertIsNone(result)

    async def test_validate_playlist_rejects_a_url_with_no_list_id(self):
        runtime = SimpleNamespace(client=SimpleNamespace(get=AsyncMock()))
        result = await validate_playlist(runtime, 'https://www.youtube.com/playlist', 'Storage Engines', 'Book')
        self.assertIsNone(result)

    async def test_validate_playlist_rejects_a_non_youtube_domain(self):
        runtime = SimpleNamespace(client=SimpleNamespace(get=AsyncMock()))
        result = await validate_playlist(runtime, 'https://not-youtube.example.com/playlist?list=xyz', 'Storage Engines', 'Book')
        self.assertIsNone(result)
        runtime.client.get.assert_not_called()

    async def test_validate_playlist_rejects_an_off_topic_playlist(self):
        html = '<html><head><meta property="og:title" content="Ten Tips for Better Sleep - YouTube"></head><body>ok</body></html>'
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text=html, url='https://www.youtube.com/playlist?list=xyz')))
        runtime = SimpleNamespace(client=client)
        result = await validate_playlist(runtime, 'https://www.youtube.com/playlist?list=xyz', 'Storage Engines', 'Kubernetes Fundamentals')
        self.assertIsNone(result)

    async def test_validate_playlist_never_reaches_a_private_address(self):
        runtime = SimpleNamespace(client=SimpleNamespace(get=AsyncMock()))
        with patch('app.research.public_url', side_effect=ProviderError('blocked')):
            result = await validate_playlist(runtime, 'https://www.youtube.com/playlist?list=xyz', 'Storage', 'Book')
        self.assertIsNone(result)
        runtime.client.get.assert_not_called()

    async def test_validate_article_rejects_short_and_non_html_content(self):
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text='<html><body><p>Too short.</p></body></html>', url='https://example.com/a')))
        runtime = SimpleNamespace(client=client)
        self.assertIsNone(await validate_article(runtime, 'https://example.com/a', 'Storage Engines', 'Book'))
        client_binary = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text='binary', headers={'content-type':'application/pdf'}, url='https://example.com/a.pdf')))
        runtime_binary = SimpleNamespace(client=client_binary)
        self.assertIsNone(await validate_article(runtime_binary, 'https://example.com/a.pdf', 'Storage Engines', 'Book'))

    async def test_validate_article_accepts_a_real_relevant_article(self):
        paragraph = ' '.join(['Storage engines organize data on disk for fast retrieval.'] * 10)
        html = f'<html><head><title>Storage Engines Explained</title></head><body><article><h1>Storage Engines Explained</h1><p>{paragraph}</p></article></body></html>'
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text=html, url='https://example.com/storage')))
        runtime = SimpleNamespace(client=client)
        result = await validate_article(runtime, 'https://example.com/storage', 'Storage Engines', 'Book')
        self.assertIsNotNone(result)

    async def test_validate_article_rejects_off_topic_and_blocked_domains(self):
        paragraph = ' '.join(['This article is about gardening and houseplants in spring.'] * 10)
        html = f'<html><head><title>Gardening Tips</title></head><body><article><p>{paragraph}</p></article></body></html>'
        client = SimpleNamespace(get=AsyncMock(return_value=fake_response(200, text=html, url='https://example.com/garden')))
        runtime = SimpleNamespace(client=client)
        self.assertIsNone(await validate_article(runtime, 'https://example.com/garden', 'Storage Engines', 'Book'))
        blocked_runtime = SimpleNamespace(client=SimpleNamespace(get=AsyncMock()))
        self.assertIsNone(await validate_article(blocked_runtime, 'https://www.reddit.com/r/storage', 'Storage Engines', 'Book'))
        blocked_runtime.client.get.assert_not_called()

    async def test_validate_article_never_reaches_a_private_address(self):
        runtime = SimpleNamespace(client=SimpleNamespace(get=AsyncMock()))
        result = await validate_article(runtime, 'http://127.0.0.1/secret', 'Storage Engines', 'Book')
        self.assertIsNone(result)
        runtime.client.get.assert_not_called()


class CandidateDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_finds_several_validated_candidates_per_category_not_just_the_first(self):
        unit = {'number': '1', 'title': 'Storage Engines', 'page': 1, 'chapters': []}
        raw_results = {
            'video': [{'url': f'https://www.youtube.com/watch?v=vid{n:06d}', 'search_title': ''} for n in range(3)],
            'playlist_en': [{'url': f'https://www.youtube.com/playlist?list=en{n}', 'search_title': ''} for n in range(3)],
            'playlist_ar': [{'url': f'https://www.youtube.com/playlist?list=ar{n}', 'search_title': ''} for n in range(3)],
            'article_or_demo': [{'url': f'https://example.com/a{n}', 'search_title': ''} for n in range(3)],
        }
        async def fake_search(_runtime, queries, _limit, language='en'):
            query = queries[0]
            if 'youtube.com/playlist' in query and language == 'ar': return raw_results['playlist_ar']
            if 'youtube.com/playlist' in query: return raw_results['playlist_en']
            if 'youtube.com' in query: return raw_results['video']
            return raw_results['article_or_demo']
        async def always_valid(_runtime, url, *_refs):
            return {'url': url, 'title': 'Storage Engines walkthrough', 'publisher': 'example.com', 'validation_reason': 'ok'}
        with patch('app.research.search_web', side_effect=fake_search), \
             patch('app.research.validate_video', side_effect=always_valid), \
             patch('app.research.validate_playlist', side_effect=always_valid), \
             patch('app.research.validate_article', side_effect=always_valid):
            resources = await companion_resource_candidates(SimpleNamespace(), 'A general book', [unit])
        by_type = {}
        for r in resources: by_type.setdefault(r['resource_type'], []).append(r)
        self.assertEqual(set(by_type), {'video', 'playlist_en', 'playlist_ar', 'article_or_demo'})
        self.assertGreater(len(by_type['playlist_en']), 1, 'must surface more than a single first-hit candidate')

    async def test_never_fabricates_a_browse_link_when_nothing_validates(self):
        unit = {'number': '1', 'title': 'Storage Engines', 'page': 1, 'chapters': []}
        async def empty_search(*_a, **_k): return []
        with patch('app.research.search_web', side_effect=empty_search):
            resources = await companion_resource_candidates(SimpleNamespace(), 'A general book', [unit])
        self.assertEqual(resources, [])

    async def test_caps_candidates_per_category(self):
        unit = {'number': '1', 'title': 'Storage Engines', 'page': 1, 'chapters': []}
        many = [{'url': f'https://www.youtube.com/watch?v=vid{n:06d}', 'search_title': ''} for n in range(8)]
        async def fake_search(_runtime, queries, _limit, language='en'): return many
        async def always_valid(_runtime, url, *_refs):
            return {'url': url, 'title': 'Storage Engines walkthrough', 'publisher': 'example.com', 'validation_reason': 'ok'}
        with patch('app.research.search_web', side_effect=fake_search), \
             patch('app.research.validate_video', side_effect=always_valid), \
             patch('app.research.validate_playlist', side_effect=always_valid), \
             patch('app.research.validate_article', side_effect=always_valid):
            resources = await companion_resource_candidates(SimpleNamespace(), 'A general book', [unit])
        counts = {}
        for r in resources: counts[r['resource_type']] = counts.get(r['resource_type'], 0) + 1
        for count in counts.values(): self.assertLessEqual(count, 6)

    async def test_deduplicates_the_same_url_within_a_category(self):
        unit = {'number': '1', 'title': 'Storage Engines', 'page': 1, 'chapters': []}
        duplicated = [{'url': 'https://www.youtube.com/watch?v=samevideo1', 'search_title': ''} for _ in range(4)]
        async def fake_search(_runtime, queries, _limit, language='en'): return duplicated
        async def always_valid(_runtime, url, *_refs):
            return {'url': url, 'title': 'Storage Engines walkthrough', 'publisher': 'example.com', 'validation_reason': 'ok'}
        with patch('app.research.search_web', side_effect=fake_search), \
             patch('app.research.validate_video', side_effect=always_valid), \
             patch('app.research.validate_playlist', side_effect=always_valid), \
             patch('app.research.validate_article', side_effect=always_valid):
            resources = await companion_resource_candidates(SimpleNamespace(), 'A general book', [unit])
        video_urls = [r['url'] for r in resources if r['resource_type'] == 'video']
        self.assertEqual(len(video_urls), len(set(video_urls)))


class FinalizeDraftTests(unittest.TestCase):
    def payload(self):
        return {'document_title': 'Book', 'source_url': 'https://example.com/book', 'research_date': '2026-09-12',
                'level': 'intermediate', 'goal': 'learn', 'units': [
                    {'number': '1', 'title': 'Storage Engines', 'page': 1, 'members': 'Chapter 1',
                     'source_url': 'https://example.com/book#page=1', 'content': '## Unit 1 body'}]}

    def test_only_selected_resources_appear_in_the_published_document(self):
        resources = [
            {'chapter': 'Storage Engines', 'resource_type': 'video', 'title': 'Selected video', 'url': 'https://youtube.com/watch?v=a', 'publisher': 'x', 'selected': True},
            {'chapter': 'Storage Engines', 'resource_type': 'playlist_en', 'title': 'Unselected playlist', 'url': 'https://youtube.com/playlist?list=b', 'publisher': 'y', 'selected': False},
        ]
        result = companion_document_markdown(self.payload(), resources)
        self.assertIn('Selected video', result)
        self.assertNotIn('Unselected playlist', result)

    def test_unit_with_no_selection_gets_the_honest_fallback_not_a_fake_link(self):
        result = companion_document_markdown(self.payload(), [])
        self.assertIn('No useful external resource was retained for this chapter', result)
        self.assertNotIn('youtube.com/results?search_query', result)

    def test_preview_draft_never_bakes_in_links_before_curation(self):
        result = companion_preview_draft(self.payload())
        self.assertIn('chosen in the **Resources** tab', result)
        self.assertNotIn('Learn another way', result)


class SelectionEndpointTests(DatabaseCase):
    async def job_with_candidates(self):
        api=await self.signup()
        job=(await api.post('/api/research',json={'topic':'Kubernetes'})).json()['id']
        with self.owner() as conn:
            workspace=conn.execute('SELECT workspace_id FROM research_jobs').fetchone()[0]
            conn.execute("UPDATE research_jobs SET status='review'")
            for i in range(5):
                conn.execute('''INSERT INTO research_resources (workspace_id,job_id,id,chapter,title,url,resource_type,publisher,validation_reason)
                                VALUES (%s,%s,%s,'Unit 1',%s,%s,'video','x','ok')''',
                             (workspace,__import__('uuid').UUID(job),f'R{i}',f'Title {i}',f'https://youtube.com/watch?v={i}'))
        return api,job

    async def select(self,api,job,resource,selected=True):
        return await api.post(f'/api/research/{job}/resources/{resource}/selection',json={'selected':selected})

    async def test_selection_is_capped_at_four_per_category(self):
        api,job=await self.job_with_candidates()
        for i in range(4):
            result=(await self.select(api,job,f'R{i}')).json()
            self.assertTrue(next(r for r in result['resources'] if r['id']==f'R{i}')['selected'])
        self.assertEqual((await self.select(api,job,'R4')).status_code,409)

    async def test_deselecting_frees_a_slot(self):
        api,job=await self.job_with_candidates()
        for i in range(4):
            await self.select(api,job,f'R{i}')
        await self.select(api,job,'R0',False)
        result=(await self.select(api,job,'R4')).json()
        self.assertTrue(next(r for r in result['resources'] if r['id']=='R4')['selected'])

    async def test_selecting_a_resource_outside_the_job_404s(self):
        api,job=await self.job_with_candidates()
        self.assertEqual((await self.select(api,job,'does-not-exist')).status_code,404)

    async def test_another_user_cannot_touch_the_job(self):
        api,job=await self.job_with_candidates()
        stranger=await self.signup('stranger@example.com')
        self.assertEqual((await self.select(stranger,job,'R0')).status_code,404)
        self.assertEqual((await stranger.get(f'/api/research/{job}')).status_code,404)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ResearchStorageTests(DatabaseCase):
    async def test_same_source_can_belong_to_independent_projects(self):
        api=await self.signup()
        ids=[]
        for topic in ('HTTP caching','Database indexes'):
            response=await api.post('/api/research',json={'topic':topic})
            self.assertEqual(response.status_code,202,response.text)
            ids.append(response.json()['id'])
        with self.owner() as conn:
            workspace=conn.execute('SELECT workspace_id FROM research_jobs LIMIT 1').fetchone()[0]
            for job in ids:
                conn.execute('''INSERT INTO research_sources (workspace_id,job_id,id,title,url,publisher,retrieved_at,source_type,retained,excerpt)
                                VALUES (%s,%s,'S0123456789','Title','https://example.com','example.com',now(),'web',true,'Evidence')''',
                             (workspace,__import__('uuid').UUID(job)))
            self.assertEqual(conn.execute('SELECT count(*) FROM research_sources').fetchone()[0],2)

    async def test_a_repeated_create_with_the_same_key_returns_the_same_project(self):
        api=await self.signup()
        headers={'Idempotency-Key':'create-once-1234'}
        first=await api.post('/api/research',json={'topic':'HTTP caching'},headers=headers)
        second=await api.post('/api/research',json={'topic':'HTTP caching'},headers=headers)
        self.assertEqual(first.json()['id'],second.json()['id'])
        different=await api.post('/api/research',json={'topic':'Something else'},headers=headers)
        self.assertEqual(different.status_code,422)
        self.assertEqual(len((await api.get('/api/research')).json()),1)
