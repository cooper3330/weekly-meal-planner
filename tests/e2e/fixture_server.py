"""A loopback server for the fixture pages, shared by the browser tests (#102, #103).

Like the app (mealplanner/app.py) it sends the three security headers on every page, except the
page named ``no_referrer*``, which leaves out Referrer-Policy on purpose.
"""

import http.server
import threading
import time
from pathlib import Path
from typing import Any

SITE = Path(__file__).parent / "fixture_site"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)
SECURITY_HEADERS = {
    # The fixture pages use inline styles and handlers, which the app's own policy would block.
    "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class Server:
    def __init__(self, external: str = "") -> None:
        outer = self
        self.hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits.append(self.path)
                if self.path == "/slow.html":
                    time.sleep(8)
                headers = dict(SECURITY_HEADERS)
                if self.path == "/ok.png":
                    body, kind = PNG, "image/png"
                else:
                    page = SITE / self.path.lstrip("/")
                    if not page.is_file():
                        self.send_error(404)
                        return
                    body = page.read_text().replace("__EXTERNAL__", external).encode()
                    kind = "text/html; charset=utf-8"
                    if page.name.startswith("no_referrer"):
                        del headers["Referrer-Policy"]
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
