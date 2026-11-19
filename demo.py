"""Loopback-only fictional outage and recovery. No external requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from health_monitor import check


def run_demo(emit=None):
    class Handler(BaseHTTPRequestHandler):
        attempts = 0

        def do_GET(self):
            if self.path == '/recovering':
                Handler.attempts += 1
                status = 503 if Handler.attempts == 1 else 200
            else:
                status = 503 if self.path == '/unavailable' else 200
            self.send_response(status)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        return [check(base+path, service=label, retries=1, backoff=.01, emit=emit)
                for path, label in [('/ok', 'fictional-healthy'), ('/recovering', 'fictional-recovering'), ('/unavailable', 'fictional-outage')]]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
