"""
Local stand-in for the reverse proxy of DEPLOYMENT.md (deploy/Caddyfile), for the deployment
rehearsal only (scripts/e2e_run.py, environment "rehearsal"). It is NOT a production server.

It applies the same rules as the Caddyfile, on 127.0.0.1 only and without TLS:
- /api/<path>      → the backend at <path> (prefix removed), with X-Forwarded-For/-Proto/-Host;
- /static/<file>   → the hashed build file, cached as immutable; a missing file is 404;
- anything else    → the file if it exists, otherwise index.html (single-page routing), not cached;
- static responses carry the site's content security policy and the security headers; API
  responses keep the backend's own headers; no server banner;
- only GET and HEAD reach static files (405 otherwise).

    python deploy/rehearsal_proxy.py --www <bundle>/deploy/www --backend http://127.0.0.1:<port> --port <port>
"""

import argparse
import http.client
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

SITE_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
                                "form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}
MAX_BODY = 1024 * 1024


def make_handler(www: Path, backend: str):
    target = urlsplit(backend)
    root = www.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = ""
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def version_string(self) -> str:   # no server banner
            return ""

        def log_message(self, format, *args):   # quiet: the tests read responses, not logs
            pass

        def send_header(self, keyword, value):
            if keyword.lower() == "server":
                return
            super().send_header(keyword, value)

        # ── /api → backend ───────────────────────────────────────────────────
        def _proxy(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_error(413)
                return
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            headers.update({"X-Forwarded-For": self.client_address[0], "X-Forwarded-Proto": "http",
                            "X-Forwarded-Host": self.headers.get("Host", "")})
            connection = http.client.HTTPConnection(target.hostname, target.port, timeout=60)
            try:
                connection.request(self.command, self.path[len("/api"):] or "/", body=body, headers=headers)
                response = connection.getresponse()
                payload = response.read()
            except OSError:
                self.send_error(502)
                return
            finally:
                connection.close()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP and key.lower() != "server":
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        # ── static site with single-page routing ─────────────────────────────
        def _static(self):
            path = urlsplit(self.path).path
            candidate = (root / path.lstrip("/")).resolve()
            inside = candidate == root or root in candidate.parents
            if path.startswith("/static/"):
                if not (inside and candidate.is_file()):
                    self.send_error(404)
                    return
                file, cache = candidate, "public, max-age=31536000, immutable"
            elif inside and candidate.is_file():
                file, cache = candidate, "no-cache"
            else:
                file, cache = root / "index.html", "no-cache"
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            for key, value in SITE_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _dispatch(self):
            if self.path == "/api" or self.path.startswith("/api/"):
                self._proxy()
            elif self.command in ("GET", "HEAD"):
                self._static()
            else:
                self.send_response(405)
                self.send_header("Allow", "GET, HEAD")
                self.send_header("Content-Length", "0")
                self.end_headers()

        do_GET = do_HEAD = do_POST = do_PATCH = do_PUT = do_DELETE = do_OPTIONS = _dispatch

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--www", type=Path, required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    if not (args.www / "index.html").is_file():
        print(f"REFUSED: {args.www} has no index.html", file=sys.stderr)
        return 2
    mimetypes.add_type("text/javascript", ".js")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.www, args.backend))
    print(f"rehearsal proxy on http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
