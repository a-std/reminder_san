"""Minimal health endpoint server for Discord bots.

Usage:
    from health_server import start_health_server
    start_health_server(port=18790)  # Call before bot.run()

Runs an HTTP server in a daemon thread. GET /health returns 200 OK.
"""

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress request logs


def start_health_server(port: int = 18790):
    """Start a health check HTTP server in a background thread."""
    def _run():
        try:
            server = HTTPServer(("127.0.0.1", port), _HealthHandler)
            logger.info(f"Health server started on port {port}")
            server.serve_forever()
        except Exception as e:
            logger.error(f"Health server failed: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
