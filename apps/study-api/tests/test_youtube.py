"""YouTube resources: what is shown, and what must never be shown.

The fixtures below copy the shapes real YouTube pages actually return — an og:image that
points at the first video, the playlist count buried in a script tag, and the consent
shell that carries no data at all. Nothing here touches the network.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup
import httpx

from app.research import (playlist_details, validate_article, validate_playlist,
                          validate_video, youtube_host, youtube_thumbnail)


def page(html):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'aside', 'noscript', 'svg']):
        tag.decompose()
    return html, soup


# A real playlist page: metadata in the head, the interesting data inside a script.
PLAYLIST = '''<html><head>
<meta property="og:title" content="C Programming Tutorials - YouTube">
<meta property="og:image" content="https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg">
</head><body><script>var data = {"contents":{"videoId":"Bz4MxDeEM6k"},
"stats":[{"runs":[{"text":"101"}]},{"simpleText":"12,345 views"}]};</script>
<h1>C Programming Tutorials</h1></body></html>'''

# Some pages only expose the thumbnail; the id must still be recovered from it.
PLAYLIST_NO_SCRIPT = '''<html><head>
<meta property="og:title" content="Flutter Tutorial for Beginners - YouTube">
<meta property="og:image" content="https://i.ytimg.com/vi/1ukSR1GRtMU/hqdefault.jpg">
</head><body><p>36 videos</p></body></html>'''

# The consent/shell page YouTube serves to plain HTTP clients carries nothing useful.
PLAYLIST_EMPTY = '<html><head><title>YouTube</title></head><body></body></html>'

PLAYLIST_GONE = '''<html><head>
<meta property="og:title" content="C Programming Tutorials - YouTube">
</head><body>This playlist does not exist.</body></html>'''


class PlaylistDetailTests(unittest.TestCase):
    def test_first_video_and_count_come_from_the_page(self):
        raw, soup = page(PLAYLIST)
        first, count = playlist_details(raw, soup)
        self.assertEqual(first, 'Bz4MxDeEM6k')
        self.assertEqual(count, 101)
        self.assertEqual(youtube_thumbnail(first),
                         'https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg')

    def test_thumbnail_alone_is_enough_to_recover_the_first_video(self):
        raw, soup = page(PLAYLIST_NO_SCRIPT)
        first, count = playlist_details(raw, soup)
        self.assertEqual(first, '1ukSR1GRtMU')
        self.assertEqual(count, 36)

    def test_counts_with_thousands_separators_are_read_as_numbers(self):
        raw, soup = page(PLAYLIST.replace('"text":"101"', '"text":"1,204"'))
        self.assertEqual(playlist_details(raw, soup)[1], 1204)

    def test_a_page_without_data_reports_nothing_rather_than_guessing(self):
        raw, soup = page(PLAYLIST_EMPTY)
        self.assertEqual(playlist_details(raw, soup), (None, None))

    def test_stripping_scripts_must_not_lose_the_count(self):
        """The reader strips <script> for articles; the raw markup is kept for this."""
        raw, soup = page(PLAYLIST)
        self.assertNotIn('stats', str(soup))
        self.assertEqual(playlist_details(raw, soup)[1], 101)


class HostTests(unittest.TestCase):
    def test_youtube_hosts_are_recognised_and_look_alikes_are_not(self):
        for url in ('https://www.youtube.com/watch?v=abc123',
                    'https://youtu.be/abc123',
                    'https://m.youtube.com/watch?v=abc123'):
            self.assertTrue(youtube_host(url), url)
        for url in ('https://youtube.com.evil.test/watch?v=abc123',
                    'https://notyoutube.com/watch?v=abc123',
                    'https://vimeo.com/12345'):
            self.assertFalse(youtube_host(url), url)


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, html=PLAYLIST):
        raw, soup = page(html)
        client = MagicMock()
        return SimpleNamespace(client=client), (str(raw), soup, raw)

    async def test_a_playlist_carries_its_thumbnail_and_count(self):
        runtime, fetched = self.runtime()
        with patch('app.research.fetch_html', AsyncMock(return_value=fetched)):
            result = await validate_playlist(runtime,
                'https://www.youtube.com/playlist?list=PL_c9BZzLwBRKKqOc9TJz1pP0ASrxLMtp2',
                'C Programming', 'Programming basics')
        self.assertEqual(result['url'],
                         'https://www.youtube.com/playlist?list=PL_c9BZzLwBRKKqOc9TJz1pP0ASrxLMtp2')
        self.assertEqual(result['title'], 'C Programming Tutorials')
        self.assertEqual(result['video_count'], 101)
        self.assertEqual(result['thumbnail'],
                         'https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg')
        self.assertIn('101 videos', result['validation_reason'])

    async def test_an_unavailable_playlist_is_never_offered(self):
        runtime, fetched = self.runtime(PLAYLIST_GONE)
        with patch('app.research.fetch_html', AsyncMock(return_value=fetched)):
            result = await validate_playlist(runtime,
                'https://www.youtube.com/playlist?list=PLgone', 'C Programming')
        self.assertIsNone(result)

    async def test_an_off_topic_playlist_is_rejected_even_though_it_is_real(self):
        runtime, fetched = self.runtime()
        with patch('app.research.fetch_html', AsyncMock(return_value=fetched)):
            result = await validate_playlist(runtime,
                'https://www.youtube.com/playlist?list=PLreal', 'Baking sourdough bread')
        self.assertIsNone(result)

    async def test_an_unreachable_playlist_returns_nothing_instead_of_raising(self):
        runtime, _ = self.runtime()
        with patch('app.research.fetch_html', AsyncMock(side_effect=httpx.ConnectError('down'))):
            result = await validate_playlist(runtime,
                'https://www.youtube.com/playlist?list=PLreal', 'C Programming')
        self.assertIsNone(result)

    async def test_a_playlist_without_data_still_validates_without_inventing_a_count(self):
        runtime, fetched = self.runtime(PLAYLIST_EMPTY.replace(
            '<title>YouTube</title>',
            '<title>C Programming Tutorials - YouTube</title>'))
        with patch('app.research.fetch_html', AsyncMock(return_value=fetched)):
            result = await validate_playlist(runtime,
                'https://www.youtube.com/playlist?list=PLreal', 'C Programming')
        self.assertEqual(result['title'], 'C Programming Tutorials')
        self.assertIsNone(result['video_count'])
        self.assertEqual(result['thumbnail'], '')
        self.assertNotIn('None', result['validation_reason'])

    async def test_a_single_video_carries_its_own_thumbnail(self):
        runtime = SimpleNamespace(client=MagicMock())
        runtime.client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={'title': 'C Programming Pointers',
                                         'author_name': 'Caleb Curry'})))
        result = await validate_video(runtime,
            'https://www.youtube.com/watch?v=Bz4MxDeEM6k', 'C Programming')
        self.assertEqual(result['thumbnail'],
                         'https://i.ytimg.com/vi/Bz4MxDeEM6k/hqdefault.jpg')
        self.assertEqual(result['video_count'], 1)
        self.assertEqual(result['publisher'], 'Caleb Curry')
        self.assertEqual(result['url'], 'https://www.youtube.com/watch?v=Bz4MxDeEM6k')

    async def test_a_playlist_url_is_not_treated_as_a_single_video(self):
        runtime = SimpleNamespace(client=MagicMock())
        runtime.client.get = AsyncMock()
        result = await validate_video(runtime,
            'https://www.youtube.com/watch?v=abc123&list=PLxyz', 'C Programming')
        self.assertIsNone(result)
        runtime.client.get.assert_not_awaited()

    async def test_a_deleted_video_is_never_offered(self):
        runtime = SimpleNamespace(client=MagicMock())
        runtime.client.get = AsyncMock(return_value=MagicMock(status_code=404))
        result = await validate_video(runtime,
            'https://www.youtube.com/watch?v=Bz4MxDeEM6k', 'C Programming')
        self.assertIsNone(result)

    async def test_articles_declare_the_same_fields_so_the_ui_can_render_one_list(self):
        runtime = SimpleNamespace(client=MagicMock())
        source = {'url': 'https://example.com/c-programming', 'title': 'C Programming Guide',
                  'publisher': 'example.com'}
        with patch('app.research.fetch_source', AsyncMock(return_value=source)):
            result = await validate_article(runtime,
                'https://example.com/c-programming', 'C Programming')
        self.assertEqual(result['thumbnail'], '')
        self.assertIsNone(result['video_count'])

    async def test_a_youtube_url_is_never_read_as_an_article(self):
        runtime = SimpleNamespace(client=MagicMock())
        with patch('app.research.fetch_source', AsyncMock()) as fetched:
            result = await validate_article(runtime,
                'https://www.youtube.com/watch?v=Bz4MxDeEM6k', 'C Programming')
        self.assertIsNone(result)
        fetched.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
