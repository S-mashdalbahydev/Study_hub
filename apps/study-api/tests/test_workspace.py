import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import StreamingResponse

from app.ingestion import parse_markdown
from app.storage import LocalStorage
from app.structure import PREVIEW_VERSION, build_structure, source_structure
from tests.support import DatabaseCase, seed_book

EXAMPLES = Path(__file__).resolve().parents[3]/'examples'


class WorkspaceTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.api = await self.signup()
        self.a = await seed_book(self, self.api, 'a', kind='txt', content=b'Source a', chunks=[{'text': 'Source a', 'page': 1}])
        self.b = await seed_book(self, self.api, 'b', kind='txt', content=b'Source b', chunks=[{'text': 'Source b', 'page': 1}])

    async def test_restore_and_new_session_are_distinct(self):
        a = (await self.api.post('/api/sessions', json={'document_id': self.a['id']})).json()
        self.assertEqual((await self.api.patch('/api/sessions/'+a['id'], json={'page': 2, 'mode': 'socratic', 'layout': 'reading', 'zoom': 1.3})).status_code, 200)
        restored = (await self.api.post('/api/sessions', json={'document_id': self.a['id']})).json()
        self.assertEqual(restored['id'], a['id'])
        self.assertEqual((restored['page'], restored['mode'], restored['layout'], restored['zoom']), (2, 'socratic', 'reading', 1.3))
        b = (await self.api.post('/api/sessions', json={'document_id': self.b['id']})).json()
        self.assertNotEqual(a['id'], b['id'])
        fresh = (await self.api.post('/api/sessions', json={'document_id': self.a['id'], 'new': True})).json()
        self.assertNotEqual(a['id'], fresh['id'])
        self.assertEqual(fresh['page'], 1)
        self.assertEqual((await self.api.patch('/api/sessions/'+a['id'], json={'page': 0})).status_code, 422)

    async def test_sessions_are_private_to_their_author(self):
        mine = (await self.api.post('/api/sessions', json={'document_id': self.a['id']})).json()
        other = await self.signup('other@example.com')
        self.assertEqual((await other.get('/api/sessions/'+mine['id'])).status_code, 404)
        self.assertEqual((await other.get('/api/sessions')).json(), [])

    async def test_pdf_can_open_before_ai_processing_finishes(self):
        early = await seed_book(self, self.api, 'early', ready=False)
        opened = await self.api.post('/api/sessions', json={'document_id': early['id']})
        self.assertEqual(opened.status_code, 200)
        reply = await self.api.post(f"/api/sessions/{opened.json()['id']}/chat", json={'text': 'Explain this'})
        self.assertEqual(reply.status_code, 409)
        self.assertIn('keep reading', reply.json()['detail'])

    async def test_chat_persists_structured_citations_and_server_scope(self):
        s = (await self.api.post('/api/sessions', json={'document_id': self.a['id']})).json()
        seeded = self.a

        async def mocked(runtime, body, ctx, sink, choice):
            self.assertTrue(body.model.startswith('studyhub-doc-'+seeded['id']+'--'))
            self.assertEqual(body.current_page, 1)
            self.assertEqual(body.selected_text, 'Source a')
            sink.append({'id': 1, 'label': 'p. 1', 'page': 1, 'document_id': seeded['id'], 'url': 'x',
                         'chunk_id': seeded['chunks'][0], 'revision_id': seeded['revision'], 'blob_id': seeded['blob']})

            async def stream():
                for text in ['An answer', '\n\n---\nSources retrieved from this document:\n- [1 · p. 1](/backend/x)']:
                    yield 'data: '+json.dumps({'choices': [{'delta': {'content': text}}]})+'\n\n'
            return StreamingResponse(stream())
        with patch('app.workspace.complete', mocked):
            r = await self.api.post('/api/sessions/'+s['id']+'/chat', json={'text': 'Explain', 'selected_text': 'Source a', 'intent': 'explain'})
        parts = [json.loads(p[6:]) for p in r.text.split('\n\n') if p.startswith('data: ')]
        self.assertIn('citations', [p['type'] for p in parts])
        restored = (await self.api.get('/api/sessions/'+s['id'])).json()
        self.assertEqual(restored['messages'][1]['content'], 'An answer')
        self.assertEqual(restored['messages'][1]['status'], 'complete')
        self.assertEqual(restored['messages'][1]['citations'][0]['document_id'], self.a['id'])
        self.assertTrue(restored['messages'][1]['citations'][0]['url'].startswith('/backend/api/documents/'))
        b = (await self.api.post('/api/sessions', json={'document_id': self.b['id']})).json()
        self.assertEqual((await self.api.get('/api/sessions/'+b['id'])).json()['messages'], [])

    async def test_one_reply_at_a_time_per_session(self):
        s = (await self.api.post('/api/sessions', json={'document_id': self.a['id']})).json()
        with self.owner() as conn:
            workspace = conn.execute('SELECT workspace_id FROM study_sessions').fetchone()[0]
            conn.execute("INSERT INTO messages (workspace_id, session_id, role, content, status) VALUES (%s, %s, 'assistant', '', 'generating')",
                         (workspace, __import__('uuid').UUID(s['id'])))
        reply = await self.api.post('/api/sessions/'+s['id']+'/chat', json={'text': 'Again'})
        self.assertEqual(reply.status_code, 409)

    async def test_markdown_reader_preserves_source_and_builds_anchor_outline(self):
        content = '# Learning Companion\n\n## Chapter 1: Storage\n\n```text\nclient -> cache -> disk\n```\n\n- [Watch](https://example.com/video)'
        md = await seed_book(self, self.api, 'companion', kind='md', content=content.encode(), chunks=[{'text': content}])
        response = await self.api.get(f"/api/documents/{md['id']}/reader")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['content'], content)
        self.assertEqual(result['outline'][1], {'title': 'Chapter 1: Storage', 'page': 1, 'level': 2, 'anchor': 'chapter-1-storage'})
        self.assertEqual(result['book']['pages'][0]['number'], 2)
        self.assertEqual(result['book']['toc'][0]['kind'], 'chapter')
        self.assertEqual(result['book']['toc'][0]['id'], 'chapter-1-storage')
        self.assertEqual(result['book']['page_count'], 3)
        parsed = parse_markdown(content)
        self.assertIn('```text', parsed['texts'][1]['text'])

    def _library(self):
        """Two books whose chapters split cleanly into a scaling and a validating topic."""
        plan = {'a': [('Chapter 1. Scaling Out', 'scaling throughput load', 1),
                      ('Chapter 2. Validating Schemas', 'validating checks proof', 20)],
                'b': [('GROWTH ENGINES', 'scaling throughput load', 1),
                      ('TESTING ASSUMPTIONS', 'validating checks proof', 20)]}
        return {ident: ([{'id': re.sub(r'\W+', '-', title.lower()), 'title': title, 'page': page, 'level': 1, 'parent_id': None}
                         for title, _, page in chapters],
                        [{'text': (words+' ')*160, 'page': page} for _, words, page in chapters])
                for ident, chapters in plan.items()}

    async def _map(self):
        books = {}
        for ident, (outline, chunks) in self._library().items():
            books[ident] = await seed_book(self, self.api, 'Book '+ident, outline=outline, chunks=chunks)

        class TopicEmbeddings:
            """Two dimensions: how much a text is about scaling, and about validating."""
            async def embed(self, texts, *, query=False, title=''):
                return [[float(any(w in t.lower() for w in ('scal', 'throughput', 'growth'))),
                         float(any(w in t.lower() for w in ('valid', 'testing')))] for t in texts]
        self.runtime.embeddings = TopicEmbeddings()
        created = await self.api.post('/api/learning-sessions', json={'title': 'Build and scale',
            'learning_goal': 'scale the product and validate the idea', 'document_ids': [books['a']['id'], books['b']['id']]})
        self.assertEqual(created.status_code, 201, created.text)
        sid = created.json()['id']
        result = (await self.api.post(f'/api/learning-sessions/{sid}/generate',
                                      json={'themes': ['Scaling under load', 'Validating an idea']})).json()
        return books, sid, result

    async def test_study_map_groups_real_chapters_by_topic_across_books(self):
        books, _, result = await self._map()
        self.assertEqual(result['status'], 'ready')
        themes = {theme['title']: theme for theme in result['themes']}
        self.assertEqual(set(themes), {'Scaling under load', 'Validating an idea'})
        scaling = themes['Scaling under load']
        self.assertEqual({c['document_id'] for c in scaling['chapters']}, {books['a']['id'], books['b']['id']})
        self.assertEqual({c['title'] for c in scaling['chapters']}, {'Chapter 1. Scaling Out', 'GROWTH ENGINES'})
        self.assertTrue(all(c['page'] == 1 and c['end_page'] == 20 for c in scaling['chapters']))
        self.assertTrue(all(c['match_status'] == 'matched' for c in scaling['chapters']))
        every = [c['chapter_id']+c['document_id'] for theme in result['themes'] for c in theme['chapters']]
        self.assertEqual(len(every), len(set(every)))

    async def test_marking_a_chapter_read_survives_a_reload(self):
        _, sid, result = await self._map()
        theme = result['themes'][0]
        chapter = theme['chapters'][0]
        self.assertFalse(chapter['done'])
        marked = await self.api.post(f"/api/learning-sessions/{sid}/chapters/{theme['id']}/{chapter['document_id']}/{chapter['chapter_id']}",
                                     json={'done': True})
        self.assertEqual(marked.status_code, 200)
        reloaded = (await self.api.get(f'/api/learning-sessions/{sid}')).json()
        stored = next(c for t in reloaded['themes'] for c in t['chapters']
                      if c['chapter_id'] == chapter['chapter_id'] and c['document_id'] == chapter['document_id'])
        self.assertTrue(stored['done'])
        missing = await self.api.post(f"/api/learning-sessions/{sid}/chapters/{theme['id']}/{chapter['document_id']}/not-a-chapter",
                                      json={'done': True})
        self.assertEqual(missing.status_code, 404)

    async def test_a_part_container_does_not_duplicate_the_chapters_inside_it(self):
        from app import library
        from app.notebook import chapter_units, load_item
        outline = [{'id': 'part-i', 'title': 'Part I. Foundations', 'page': 1, 'level': 1, 'parent_id': None},
                   {'id': 'chapter-1', 'title': 'Chapter 1. Storage', 'page': 2, 'level': 2, 'parent_id': 'part-i'},
                   {'id': 'chapter-2', 'title': 'Chapter 2. Retrieval', 'page': 20, 'level': 2, 'parent_id': 'part-i'}]
        book = await seed_book(self, self.api, 'Parts', outline=outline,
                               chunks=[{'text': 'storage words '*90, 'page': 2}, {'text': 'retrieval words '*90, 'page': 20}])
        from app.db import Context
        import uuid
        async with self.runtime.db.tx(Context(uuid.UUID(self.api.me['user']['id']), uuid.UUID(self.api.me['workspace']['id']))) as conn:
            item = await load_item(conn, book['item'])
        titles = [unit['title'] for unit in chapter_units(self.runtime, item)]
        self.assertEqual(titles, ['Chapter 1. Storage', 'Chapter 2. Retrieval'])


class StructureTests(unittest.TestCase):
    def test_pdf_thumbnail_is_actual_first_page_and_outline_is_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorage(Path(directory))
            info, manifest = build_structure(storage, 'pdf', EXAMPLES/'river-garden.pdf',
                                             {'texts': [{'label': 'section_header', 'text': 'Real heading', 'prov': [{'page_no': 2}]}]}, 'rev/a1')
            self.assertEqual(info['page_count'], 2)
            self.assertEqual(info['version'], PREVIEW_VERSION)
            self.assertEqual(storage.read_json(manifest['structure_key'])['outline'], info['outline'])
            self.assertGreater(len(storage.read_bytes(manifest['cover_key'])), 1000)

    def test_source_structure_preserves_hierarchy_and_extracts_figures(self):
        parsed = {'texts': [{'label': 'section_header', 'text': 'Chapter 1', 'level': 1, 'prov': [{'page_no': 2}]},
                            {'label': 'section_header', 'text': '1.1 Reliability', 'level': 2, 'prov': [{'page_no': 3}]}],
                  'pictures': [{'captions': [{'text': 'Figure 1.1 — Failure domains'}], 'prov': [{'page_no': 4, 'bbox': {'l': 1}}]}]}
        outline, figures = source_structure(parsed)
        self.assertEqual(outline[1]['parent_id'], outline[0]['id'])
        self.assertEqual(figures[0]['label'], 'Figure 1.1')
        self.assertEqual(figures[0]['section_id'], outline[1]['id'])

    def test_native_pdf_bookmarks_outrank_flat_docling_headings(self):
        import pypdfium2 as pdfium
        parsed = {'texts': [{'label': 'section_header', 'text': f'Heading {n}', 'level': 1, 'prov': [{'page_no': n}]} for n in (1, 2)]}
        with pdfium.PdfDocument(EXAMPLES/'river-garden.pdf') as pdf:
            outline, _ = source_structure(parsed, pdf)
            native = list(pdf.get_toc())
        if native:
            self.assertEqual(len(outline), len(native))
            self.assertNotIn('Heading 1', [node['title'] for node in outline])
        else:
            self.assertEqual([node['title'] for node in outline], ['Heading 1', 'Heading 2'])

    def test_flat_docling_levels_still_produce_a_nested_contents(self):
        titles = ['Part I. Foundations', 'Chapter 1. Storage', '1.1 Records', '1.1.1 Layout', 'Chapter 2. Retrieval']
        parsed = {'texts': [{'label': 'section_header', 'text': t, 'level': 1, 'prov': [{'page_no': i+1}]} for i, t in enumerate(titles)]}
        outline, _ = source_structure(parsed)
        by_title = {node['title']: node for node in outline}
        self.assertEqual(by_title['Part I. Foundations']['parent_id'], None)
        self.assertEqual(by_title['Chapter 1. Storage']['parent_id'], by_title['Part I. Foundations']['id'])
        self.assertEqual(by_title['1.1 Records']['parent_id'], by_title['Chapter 1. Storage']['id'])
        self.assertEqual(by_title['1.1.1 Layout']['parent_id'], by_title['1.1 Records']['id'])
        self.assertEqual(by_title['Chapter 2. Retrieval']['parent_id'], by_title['Part I. Foundations']['id'])
        self.assertEqual([node['level'] for node in outline], [1, 2, 3, 4, 2])

    def test_docling_reference_captions_are_resolved_not_dropped(self):
        parsed = {'texts': [{'label': 'section_header', 'text': 'Chapter 1. Storage', 'level': 1, 'prov': [{'page_no': 1}]},
                            {'self_ref': '#/texts/9', 'label': 'caption', 'text': 'Figure 1-1. One possible architecture.'}],
                  'pictures': [{'captions': [{'$ref': '#/texts/9'}], 'prov': [{'page_no': 2}]},
                               {'captions': [], 'prov': [{'page_no': 2}]}]}
        outline, figures = source_structure(parsed)
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0]['label'], 'Figure 1-1')
        self.assertEqual(figures[0]['caption'], 'One possible architecture.')
        self.assertEqual(figures[0]['section_id'], outline[0]['id'])
