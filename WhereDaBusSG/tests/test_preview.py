"""Preview HTTP behavior without sockets, external packages, or real data.

Run from the project root: python -m unittest tests.test_preview
"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import preview


class MemoryHandler(preview.PreviewHandler):
    """Capture HTTP output while retaining the actual routing implementation."""
    def __init__(self, path):
        self.path = path
        self.wfile = io.BytesIO()
        self.sent_headers = {}
        self.status = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers[name] = value

    def end_headers(self):
        pass

    def send_error(self, status, message):
        self.status = status
        self.error_message = message


class PreviewTests(unittest.TestCase):
    def setUp(self):
        # Keep fixtures in the checkout for restricted/offline test environments.
        tests_directory = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix='.preview-test-', dir=tests_directory)
        if Path(self.temp.name).resolve().parent != tests_directory:
            raise RuntimeError('Test fixture directory escaped the tests directory')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.web = root / 'web'
        self.web.mkdir()
        (self.web / 'index.html').write_text('<h1>Preview</h1>', encoding='utf-8')
        (self.web / 'demo.js').write_text('export const demo = true;', encoding='utf-8')
        (self.web / 'styles.css').write_text('body { color: black; }', encoding='utf-8')
        (root / 'private.txt').write_text('outside GUI root', encoding='utf-8')
        patcher = patch.object(preview, 'WEB_ROOT', self.web)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, path, head=False):
        handler = MemoryHandler(path)
        if head:
            handler.do_HEAD()
        else:
            handler.do_GET()
        return handler

    def test_root_serves_html_with_correct_content_length(self):
        response = self.request('/')
        self.assertEqual(response.status, 200)
        self.assertIn('text/html', response.sent_headers['Content-Type'])
        self.assertEqual(response.wfile.getvalue(), b'<h1>Preview</h1>')
        self.assertEqual(int(response.sent_headers['Content-Length']), len(response.wfile.getvalue()))

    def test_assets_have_browser_compatible_mime_types(self):
        for path, mime in [('/ui-assets/demo.js', 'text/javascript'), ('/ui-assets/styles.css', 'text/css')]:
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 200)
                self.assertIn(mime, response.sent_headers['Content-Type'])
                self.assertEqual(response.sent_headers['X-Content-Type-Options'], 'nosniff')

    def test_asset_query_string_is_ignored(self):
        self.assertEqual(self.request('/ui-assets/demo.js?v=123').wfile.getvalue(),
                         self.request('/ui-assets/demo.js').wfile.getvalue())

    def test_head_sends_headers_without_a_body(self):
        for path in ['/', '/ui-assets/demo.js', '/ui/config']:
            with self.subTest(path=path):
                response = self.request(path, head=True)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.wfile.getvalue(), b'')
                self.assertGreater(int(response.sent_headers['Content-Length']), 0)

    def test_config_clearly_identifies_preview_without_credentials(self):
        response = self.request('/ui/config')
        self.assertEqual(response.status, 200)
        self.assertIn('application/json', response.sent_headers['Content-Type'])
        self.assertEqual(response.sent_headers['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(response.wfile.getvalue()), {
            'mode': 'preview', 'configured': {'sql': False, 'mongo': False, 'lta': False},
            'ready': False, 'timezone': 'Asia/Singapore'})

    def test_live_endpoints_are_explicitly_unavailable(self):
        for path in ['/arrivals/01001', '/stops/nearby', '/health']:
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 503)
                self.assertIn('Live data is unavailable', json.loads(response.wfile.getvalue())['detail'])

    def test_missing_assets_and_directory_listing_are_rejected(self):
        for path in ['/ui-assets/missing.js', '/ui-assets/']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path).status, 404)

    def test_plain_and_encoded_traversal_cannot_read_outside_gui(self):
        for path in ['/ui-assets/../private.txt', '/ui-assets/%2e%2e/private.txt',
                     '/ui-assets/..%5cprivate.txt', '/ui-assets/%2e%2e%2fprivate.txt']:
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn(b'outside GUI root', response.wfile.getvalue())


if __name__ == '__main__':
    unittest.main()
