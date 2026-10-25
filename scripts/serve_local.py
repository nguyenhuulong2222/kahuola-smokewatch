"""Preview the public page locally against a LocalStore folder (e.g. the backfill output).

  LOCAL_STORE=out/site python scripts/serve_local.py   ->  http://127.0.0.1:8765/

/img/<key> images are served straight from the folder (in AWS a presigned-S3 redirect);
every other path goes through handler.http exactly as the Lambda Function URL would.
"""
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import handler  # noqa: E402

ROOT = os.path.abspath(os.environ.get("LOCAL_STORE", "./out"))


class H(BaseHTTPRequestHandler):
    def _go(self, method):
        u = urlsplit(self.path)
        if u.path.startswith("/img/") and not u.path.endswith(".json"):   # JSON goes through handler, as in AWS
            key = u.path[5:]
            p = os.path.abspath(os.path.join(ROOT, key))
            if not handler._IMG.match(key) or not p.startswith(ROOT) or not os.path.isfile(p):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("content-type", mimetypes.guess_type(p)[0] or "application/octet-stream")
            self.end_headers()
            self.wfile.write(open(p, "rb").read())
            return
        n = int(self.headers.get("content-length") or 0)
        ev = {"rawPath": u.path, "queryStringParameters": dict(parse_qsl(u.query)),
              "requestContext": {"http": {"method": method}},
              "body": self.rfile.read(n).decode() if n else None}
        r = handler.http(ev)
        self.send_response(r["statusCode"])
        for k, v in r["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(r["body"].encode())

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", 8765))), H).serve_forever()
