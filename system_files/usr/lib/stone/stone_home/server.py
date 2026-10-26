"""STONE Home's page transport over local HTTP (the Chromium shell; also the development server).

  GET  <base>/                 the interface (index.html) from UI_DIR
  GET  <base>/fonts/<file>     its bundled fonts
  GET  <base>/art/<appid>/<kind>
                               game artwork resolved by Host.art_path()
  GET  <base>/events           Server-Sent Events: {"name", "data"} pushed by the host
  POST <base>/api              {"method", "args"} -> {"ok", "result"} | {"ok": false, "code", "message"}

<base> is "/s/<token>" with a random token made at every start (the development server uses
""). Without the token nothing is served, so other programs on the console, or a web page in
some other browser, cannot drive STONE Home. Only 127.0.0.1 is listened on, and the Host
header must name it (no DNS rebinding). Requests are answered by Host.handle() on the GLib
main thread, as the WebKit shell does.
"""

import json
import mimetypes
import os
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gi.repository import GLib

MAX_BODY = 65536
FILE_TYPES = {".html": "text/html; charset=utf-8", ".woff2": "font/woff2", ".png": "image/png",
              ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webm": "video/webm"}


class Clients:
    """Open /events streams. send() may be called from any thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.queues = []

    def add(self):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            self.queues.append(q)
        return q

    def remove(self, q):
        with self.lock:
            if q in self.queues:
                self.queues.remove(q)

    def send(self, name, data):
        msg = json.dumps({"name": name, "data": data})
        with self.lock:
            for q in self.queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:      # a stalled page loses events, never blocks the host
                    pass


def ui_file(ui_dir, name):
    """Only regular files directly in ui_dir or ui_dir/fonts; no other path is reachable."""
    sub, _, base = name.rpartition("/")
    if sub not in ("", "fonts") or not base or base.startswith("."):
        return None
    d = os.path.join(ui_dir, sub) if sub else ui_dir
    try:
        ok = base in os.listdir(d)
    except OSError:
        return None
    p = os.path.join(d, base)
    return p if ok and os.path.isfile(p) else None


def on_main(fn, *args, timeout=60):
    """Run fn(*args, done) on the GLib main thread and wait for done()."""
    ev, box = threading.Event(), {}

    def done(result=None, code=None, message=""):
        box["r"] = {"ok": True, "result": result} if code is None else {"ok": False, "code": code, "message": message}
        ev.set()

    GLib.idle_add(lambda: (fn(*args, done), False)[1])
    if not ev.wait(timeout):
        return {"ok": False, "code": "ServiceUnavailable", "message": "timeout"}
    return box["r"]


class HomeServer:
    def __init__(self, host, ui_dir, port=0, token=None):
        """token=None makes a random one; token="" serves at / (development only)."""
        self.host = host
        self.ui_dir = ui_dir
        self.clients = Clients()
        self.token = secrets.token_urlsafe(24) if token is None else token
        self.base = f"/s/{self.token}" if self.token else ""
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def _file(self, path, cache=False):
                if not path or not os.path.isfile(path):
                    return self._send(404, b"not found", "text/plain")
                with open(path, "rb") as f:
                    body = f.read()
                ext = os.path.splitext(path)[1].lower()
                ctype = FILE_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                # Artwork and fonts do not change while STONE runs: the GPU keeps decoded copies.
                self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _path(self):
                """The path below <base>, or None if the request is not for STONE Home."""
                hosthdr = (self.headers.get("Host") or "").lower()
                port = self.server.server_address[1]
                if hosthdr not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                    return None
                p = self.path.split("?")[0].split("#")[0]
                if srv.base:
                    if p == srv.base:
                        return "/"
                    if not p.startswith(srv.base + "/"):
                        return None
                    p = p[len(srv.base):]
                return p

            def do_GET(self):
                p = self._path()
                if p is None:
                    return self._send(404, b"not found", "text/plain")
                if p in ("/", "/index.html"):
                    return self._file(ui_file(srv.ui_dir, "index.html"))
                if p.startswith("/fonts/"):
                    return self._file(ui_file(srv.ui_dir, p[1:]), cache=True)
                if p.startswith("/art/"):
                    parts = p[5:].split("/")
                    return self._file(srv.host.art_path(parts[0], parts[1]) if len(parts) == 2 else None, cache=True)
                if p == "/events":
                    return self._events()
                return self._send(404, b"not found", "text/plain")

            def _events(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                q = srv.clients.add()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15)
                            self.wfile.write(f"data: {msg}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    srv.clients.remove(q)

            def do_POST(self):
                self.close_connection = True      # a refused body is never read: no reuse
                p = self._path()
                if p != "/api":
                    return self._send(404, b"not found", "text/plain")
                origin = self.headers.get("Origin")
                port = self.server.server_address[1]
                if origin not in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                    return self._send(403, b"forbidden", "text/plain")
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if not 0 <= n <= MAX_BODY:
                    return self._send(413, b"too large", "text/plain")
                try:
                    req = json.loads(self.rfile.read(n))
                except ValueError:
                    req = None
                r = on_main(srv.host.handle, req)
                self._send(200, json.dumps(r).encode(), "application/json")

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def handle_error(self, request, client_address):
                pass                      # a page closing its connection is not an error

        self.httpd = Server(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}{self.base}/"
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
