"""A deliberately small static file server for the geng landing page.

Why not `python -m http.server`: CPython's own docs say it "is not recommended for
production. It only implements basic security checks." Concretely it serves
directory listings, speaks HTTP/1.0 so every request costs a new connection, and
sets no cache headers. This server is ~100 lines and fixes exactly those things
for the one job it has: serving a handful of static files behind a Cloudflare
tunnel that already terminates TLS.

  python serve_static.py --root C:\\srv\\graphengineering --port 8123

Binds loopback only by default, because the tunnel is the intended front door.
"""

from __future__ import annotations

import argparse
import http.server
import mimetypes
import os
import socketserver
import sys
from pathlib import Path

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
# HTML changes when the page is redeployed; assets are immutable enough to cache.
CACHE_HTML = "public, max-age=60, must-revalidate"
CACHE_ASSET = "public, max-age=86400"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive, unlike http.server's default
    server_version = "geng-static"
    sys_version = ""                        # do not advertise the Python version

    root: Path = Path.cwd()

    def resolve(self, path: str) -> Path | None:
        """Map a URL path to a file inside root, or None if it escapes."""
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if "\0" in clean:
            return None
        target = (self.root / clean.lstrip("/")).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None                     # traversal attempt
        if target.is_dir():
            target = target / "index.html"  # never a directory listing
        return target if target.is_file() else None

    def respond(self, code: int, body: bytes, ctype: str, cache: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def serve(self) -> None:
        target = self.resolve(self.path)
        if target is None:
            self.respond(404, b"<h1>404</h1>\n", "text/html; charset=utf-8", "no-store")
            return
        suffix = target.suffix.lower()
        ctype = TYPES.get(suffix) or mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
        cache = CACHE_HTML if suffix in (".html", ".md", ".txt") else CACHE_ASSET
        self.respond(200, target.read_bytes(), ctype, cache)

    do_GET = do_HEAD = serve                # HEAD is handled inside respond()

    def log_message(self, fmt: str, *args) -> None:
        """One compact line per request; the launcher decides where it goes."""
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")
        sys.stderr.flush()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    root = args.root.resolve()
    if not (root / "index.html").is_file():
        print(f"no index.html in {root}", file=sys.stderr)
        return 2
    Handler.root = root

    with Server((args.host, args.port), Handler) as srv:
        print(f"serving {root} on http://{args.host}:{args.port}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
