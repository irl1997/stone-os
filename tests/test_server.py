"""stone_home/server.py: the Chromium shell's local transport serves only with its secret path.

    python3 tests/test_server.py
"""

import http.client
import json
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "system_files", "usr", "lib", "stone"))

from gi.repository import GLib  # noqa: E402

from stone_home.server import HomeServer  # noqa: E402


class FakeHost:
    def __init__(self, art):
        self.calls = []
        self.art = art

    def handle(self, req, done):
        self.calls.append(req)
        done({"echo": req})

    def art_path(self, appid, kind):
        return self.art if (appid, kind) == ("1001", "capsule") else None


def req(srv, method, path, body=None, host=None, origin=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{srv.port}"}
    if origin:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def main():
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    fails = 0

    def check(name, cond, info=""):
        nonlocal fails
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {info}"))
        fails += not cond

    with tempfile.TemporaryDirectory() as ui:
        os.makedirs(os.path.join(ui, "fonts"))
        open(os.path.join(ui, "index.html"), "w").write("<p>stone</p>")
        open(os.path.join(ui, "fonts", "a.woff2"), "wb").write(b"font")
        open(os.path.join(ui, ".secret"), "w").write("no")
        art = os.path.join(ui, "cap.jpg")
        open(art, "wb").write(b"jpg")
        host = FakeHost(art)
        srv = HomeServer(host, ui).start()
        b = srv.base
        try:
            check("token is long and in the URL", len(srv.token) >= 24 and srv.url.endswith(f"/s/{srv.token}/"))
            s, d = req(srv, "GET", b + "/")
            check("page with the token", s == 200 and d == b"<p>stone</p>", (s, d))
            check("no page without the token", req(srv, "GET", "/")[0] == 404 and req(srv, "GET", "/index.html")[0] == 404)
            check("no page with a wrong token", req(srv, "GET", "/s/wrong/")[0] == 404)
            check("fonts and art with the token", req(srv, "GET", b + "/fonts/a.woff2") == (200, b"font")
                  and req(srv, "GET", b + "/art/1001/capsule") == (200, b"jpg"))
            check("nothing else of the folder", req(srv, "GET", b + "/.secret")[0] == 404
                  and req(srv, "GET", b + "/fonts/../.secret")[0] == 404 and req(srv, "GET", b + "/art/1001/hero")[0] == 404)
            s, d = req(srv, "POST", b + "/api", {"method": "library", "args": []}, origin=f"http://127.0.0.1:{srv.port}")
            check("api with the token", s == 200 and json.loads(d)["result"]["echo"]["method"] == "library", (s, d))
            n = len(host.calls)
            check("no api without the token", req(srv, "POST", "/api", {"method": "power", "args": ["off"]})[0] == 404)
            check("no api from another site", req(srv, "POST", b + "/api", {"method": "power", "args": ["off"]},
                                                     origin="http://evil.example")[0] == 403)
            check("no api through another host name (DNS rebinding)",
                  req(srv, "POST", b + "/api", {"method": "power", "args": ["off"]}, host="evil.example:80")[0] == 404)
            check("refused requests never reach the host", len(host.calls) == n, host.calls)
            check("oversized body refused", req(srv, "POST", b + "/api", {"x": "a" * 70000})[0] == 413)
        finally:
            srv.stop()
        dev = HomeServer(FakeHost(art), ui, token="").start()
        try:
            check("development server (no token) serves at /", req(dev, "GET", "/") == (200, b"<p>stone</p>"))
        finally:
            dev.stop()
    loop.quit()
    print("\nall passed" if not fails else f"\n{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
