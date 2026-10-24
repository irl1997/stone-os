#!/usr/bin/env python3
"""STONE Home in a normal browser, for development and CI. Not installed in the image.

Serves the production page (system_files/usr/share/stone-home/index.html) and answers its
requests with the same Host the console uses (stone_home/host.py), over HTTP instead of
WebKit's message handler:

  POST /api      {"method", "args"}  ->  {"ok", "result"} | {"ok": false, "code", "message"}
  GET  /events   Server-Sent Events: {"name", "data"}
  GET  /art/<appid>/<kind>           artwork resolved by library.art_path()

Usually run against a real stoned on the session bus with the fake governor (see
tests/e2e_home.py), and a fixture Steam folder:

  STONE_BUS=session python3 development/stone_home_devserver.py --steam-root tests/fixtures/steam \
      --fake-steam /tmp/launches.jsonl --fake-power /tmp/power.jsonl

--fake-steam records steam:// requests instead of starting Steam, and announces the game
to stoned (GameStarted) the way Steam's reaper would be seen, when stoned runs with
STONE_GAME_SOURCE=dbus. --fake-power records power requests instead of calling logind.
Listens on 127.0.0.1 only.
"""

import argparse
import json
import mimetypes
import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "system_files/usr/lib/stone"))

from gi.repository import Gio, GLib  # noqa: E402

from stone_home import host as host_mod  # noqa: E402
from stone_home import system as system_mod  # noqa: E402

UI = os.path.join(REPO, "system_files/usr/share/stone-home")


class Clients:
    def __init__(self):
        self.lock = threading.Lock()
        self.queues = []

    def add(self):
        q = queue.Queue()
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
                q.put(msg)


class FakeSystem(system_mod.System):
    def __init__(self, launches, power_log, bus):
        super().__init__(spawn=self._record)
        self.launches, self.power_log, self.sbus = launches, power_log, bus

    def _record(self, url):
        with open(self.launches, "a") as f:
            f.write(json.dumps({"url": url}) + "\n")
        if url.startswith("steam://rungameid/"):
            gid = int(url[18:])
            appid = str(gid >> 32) if gid > 0xFFFFFFFF else str(gid)
            self.sbus.call("org.stonemachine.Performance1", "/org/stonemachine/Performance1",
                           "org.stonemachine.Performance1", "GameStarted", GLib.Variant("(s)", (appid,)),
                           None, Gio.DBusCallFlags.NONE, 5000, None, None)

    def power(self, action, done):
        if action not in ("off", "reboot"):
            return done(None, "InvalidValue", "action")
        if self.power_log:
            with open(self.power_log, "a") as f:
                f.write(json.dumps({"action": action}) + "\n")
            return done({"ok": True})
        return super().power(action, done)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--steam-root", default=None)
    ap.add_argument("--fake-steam", default=None, help="record steam:// requests to this file")
    ap.add_argument("--fake-power", default=None, help="record power requests to this file")
    a = ap.parse_args()

    clients = Clients()
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION if os.environ.get("STONE_BUS") == "session" else Gio.BusType.SYSTEM, None)
    sysapi = FakeSystem(a.fake_steam, a.fake_power, bus) if a.fake_steam else None
    host = host_mod.Host(clients.send, sysapi=sysapi, steam_root=a.steam_root)

    def on_main(fn, *args):
        """Run fn on the GLib thread and wait for its done() callback."""
        ev, box = threading.Event(), {}

        def done(result=None, code=None, message=""):
            box["r"] = {"ok": True, "result": result} if code is None else {"ok": False, "code": code, "message": message}
            ev.set()

        GLib.idle_add(lambda: (fn(*args, done), False)[1])
        if not ev.wait(60):
            return {"ok": False, "code": "ServiceUnavailable", "message": "timeout"}
        return box["r"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path):
            if not path or not os.path.isfile(path):
                return self._send(404, b"not found", "text/plain")
            with open(path, "rb") as f:
                body = f.read()
            self._send(200, body, mimetypes.guess_type(path)[0] or "application/octet-stream")

        def do_GET(self):
            p = self.path.split("?")[0]
            if p in ("/", "/index.html"):
                return self._file(os.path.join(UI, "index.html"))
            if p.startswith("/fonts/") and "/" not in p[7:] and not p[7:].startswith("."):
                return self._file(os.path.join(UI, "fonts", p[7:]))
            if p.startswith("/art/"):
                parts = p[5:].split("/")
                return self._file(host.art_path(parts[0], parts[1]) if len(parts) == 2 else None)
            if p == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                q = clients.add()
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
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    clients.remove(q)
                return
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api":
                return self._send(404, b"not found", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                return self._send(413, b"too large", "text/plain")
            try:
                req = json.loads(self.rfile.read(n))
            except ValueError:
                req = None
            r = on_main(host.handle, req)
            self._send(200, json.dumps(r).encode(), "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"STONE Home dev server on http://127.0.0.1:{a.port}/", flush=True)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 15, lambda: (loop.quit(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 2, lambda: (loop.quit(), False)[1])
    loop.run()
    srv.shutdown()


if __name__ == "__main__":
    main()
