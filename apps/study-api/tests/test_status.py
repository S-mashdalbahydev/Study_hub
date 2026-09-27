import httpx

from tests.support import DatabaseCase


class StatusTests(DatabaseCase):
    async def check_stack(self, handler, expected_code):
        api = await self.signup()
        original = self.app.state.client
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
            self.app.state.client = mock
            self.runtime.catalog.client = mock
            try:
                response = await api.get('/status')
                self.assertEqual(response.status_code, expected_code)
                self.assertEqual(len(response.json()['services']), 4)
                self.assertEqual((await api.get('/health')).status_code, 200)
                return response.json()
            finally:
                self.app.state.client = original

    async def test_all_dependencies_reachable(self):
        data = await self.check_stack(lambda request: httpx.Response(200), 200)
        self.assertEqual(data['status'], 'ok')
        self.assertTrue(all(service['status'] == 'up' for service in data['services']))

    async def test_http_failure_isolated_to_dependency(self):
        targets = self.app.state.targets
        data = await self.check_stack(lambda request: httpx.Response(503 if str(request.url) == targets['docling'] else 200), 503)
        failed = [s for s in data['services'] if s['status'] == 'down']
        self.assertEqual([s['name'] for s in failed], ['docling'])
        self.assertIn('503', failed[0]['error'])

    async def test_timeout_does_not_break_status_or_liveness(self):
        targets = self.app.state.targets

        def handler(request):
            if str(request.url) == targets['searxng']:
                raise httpx.ReadTimeout('probe timed out', request=request)
            return httpx.Response(200)
        data = await self.check_stack(handler, 503)
        failed = [s for s in data['services'] if s['status'] == 'down']
        self.assertEqual([s['name'] for s in failed], ['searxng'])
        self.assertIn('ReadTimeout', failed[0]['error'])

    async def test_liveness_needs_no_sign_in(self):
        self.assertEqual((await self.client().get('/health')).status_code, 200)
