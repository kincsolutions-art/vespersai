"""Loopback-only resident worker health; no persistent readiness files."""

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg


def start_readiness(database_url: str, stopping: threading.Event) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            healthy = not stopping.is_set()
            if healthy:
                try:
                    with psycopg.connect(database_url, connect_timeout=2) as connection:
                        connection.execute("SELECT 1")
                except Exception:
                    healthy = False
            self.send_response(200 if healthy else 503)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 9091), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9091/", timeout=3) as response:
            healthy = response.status == 200
    except Exception:
        healthy = False
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
