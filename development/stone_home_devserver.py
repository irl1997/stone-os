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
Listens on 127.0.0.1 only. The HTTP side is stone_home/server.py, the one the console uses.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "system_files/usr/lib/stone"))

from gi.repository import Gio, GLib  # noqa: E402

from stone_home import host as host_mod  # noqa: E402
from stone_home import server as server_mod  # noqa: E402
from stone_home import system as system_mod  # noqa: E402

UI = os.path.join(REPO, "system_files/usr/share/stone-home")


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

    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION if os.environ.get("STONE_BUS") == "session" else Gio.BusType.SYSTEM, None)
    sysapi = FakeSystem(a.fake_steam, a.fake_power, bus) if a.fake_steam else None
    box = {}
    host = host_mod.Host(lambda name, data: box["srv"].clients.send(name, data), sysapi=sysapi, steam_root=a.steam_root)
    # Same server as the console's Chromium shell, without the token (development only).
    srv = box["srv"] = server_mod.HomeServer(host, UI, port=a.port, token="").start()
    print(f"STONE Home dev server on {srv.url}", flush=True)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 15, lambda: (loop.quit(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 2, lambda: (loop.quit(), False)[1])
    loop.run()
    srv.stop()


if __name__ == "__main__":
    main()
