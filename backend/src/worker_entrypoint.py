"""Worker entrypoint for Cloud Run.

Runs both a health check server and Celery worker.
"""

import os
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthHandler(BaseHTTPRequestHandler):
    """Simple health check handler."""

    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress access logs
        pass


def run_health_server():
    """Run the health check server."""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server running on port {port}")
    server.serve_forever()


def run_celery_worker():
    """Run the Celery worker."""
    subprocess.run([
        "celery",
        "-A", "src.celery_app",
        "worker",
        "--loglevel=info",
        "--concurrency=1",
    ])


if __name__ == "__main__":
    # Start health server in background thread
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    # Run Celery worker in main thread
    run_celery_worker()
