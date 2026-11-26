"""Run the synthetic GUI preview using only Python's standard library.

    python preview.py

Open http://127.0.0.1:8765. This server never contacts LTA or a database.
Use the FastAPI application for live data; this helper is only a local preview.
"""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


WEB_ROOT = Path(__file__).resolve().parent / 'backend' / 'web'


class PreviewHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.html': 'text/html; charset=utf-8',
        '.svg': 'image/svg+xml',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):
        self._serve(send_body=True)

    def do_HEAD(self):
        self._serve(send_body=False)

    def _json(self, value, status=200, send_body=True):
        body = json.dumps(value).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _serve(self, send_body):
        requested = unquote(urlsplit(self.path).path)
        if requested == '/ui/config':
            self._json({'mode': 'preview', 'configured': {'sql': False, 'mongo': False, 'lta': False},
                        'ready': False, 'timezone': 'Asia/Singapore'}, send_body=send_body)
            return
        if requested == '/':
            relative = 'index.html'
        elif requested.startswith('/ui-assets/'):
            relative = requested[len('/ui-assets/'):]
        else:
            self._json({'detail': 'Live data is unavailable in this local preview. Choose Demo mode or run the FastAPI app.'},
                       status=503, send_body=send_body)
            return
        # Restrict preview requests to real files below the GUI directory.
        target = (WEB_ROOT / relative).resolve()
        if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
            self.send_error(404, 'Asset not found')
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(str(target)))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description='Preview the WhereDaBus SG GUI with synthetic demo data.')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    with ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler) as server:
        print(f'WhereDaBus SG preview: http://127.0.0.1:{args.port}', flush=True)
        print('Synthetic examples only. Press Ctrl+C to stop.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
