"""STONE Home tests: Steam library reading, the STONE shortcut, the host request table,
the D-Bus bridge against a real stoned (fake governor), settings, and game detection.

    python3 tests/test_home.py [name-filter...]

Passing means CODE TESTED. Behaviour inside Steam Game Mode on the board is in
HARDWARE_VALIDATION.md and stays NOT TESTED until run there.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "system_files", "usr", "lib", "stone"))

from gi.repository import GLib  # noqa: E402

import steam_fixture as FX  # noqa: E402
from stone_home import bridge, host as host_mod, library, shortcuts as SC, system, vdf  # noqa: E402
from stoned import games  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Tmp:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="stone-home-test-")
        return self.d

    def __exit__(self, *a):
        shutil.rmtree(self.d, ignore_errors=True)


# ---------------------------------------------------------------- VDF
@test
def V01_text_vdf_nested_escapes_comments():
    d = vdf.loads('// c\n"A" { "k" "v\\"q" "B" { "x" "1" } unq "ok" [$WIN32] }')
    assert d == {"A": {"k": 'v"q', "B": {"x": "1"}, "unq": "ok"}}, d
    assert vdf.get_ci(d, "a", "b", "X") == "1"


@test
def V02_text_vdf_damaged_raises():
    for bad in ('"A" {', '"A" { "k" }', '}', '"A" "unterminated'):
        try:
            vdf.loads(bad)
        except vdf.VDFError:
            continue
        raise AssertionError(f"accepted {bad!r}")


@test
def V03_binary_vdf_roundtrip():
    obj = {"shortcuts": {"0": {"appid": 0x8ABC1234, "AppName": "Ñandú", "IsHidden": 0, "tags": {"0": "x"}}}}
    raw = vdf.binary_dumps(obj)
    assert raw.endswith(b"\x08\x08")
    assert vdf.binary_loads(raw) == obj
    try:
        vdf.binary_loads(raw[:-5])
        raise AssertionError("truncated file accepted")
    except vdf.VDFError:
        pass


# ---------------------------------------------------------------- library
@test
def L01_library_lists_games_not_tools_most_recent_first():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"))
        r = library.scan(root)
        names = [g["name"] for g in r["games"]]
        assert r["root_found"]
        assert names == ["Marea Lenta", "Basalto <b>&</b>", "Órbita Norte", "Ceniza"], names
        g = {x["name"]: x for x in r["games"]}
        assert g["Marea Lenta"]["playtime_min"] == 2520 and g["Marea Lenta"]["installed"]
        assert g["Ceniza"]["updating"] and g["Ceniza"]["installed"]
        assert g["Órbita Norte"]["source"] == "shortcut" and g["Órbita Norte"]["appid"] == str(FX.EMU_APPID)
        assert all("STONE" != n for n in names)
        assert g["Marea Lenta"]["launch"] == "steam://rungameid/1001"
        assert g["Órbita Norte"]["launch"] == f"steam://rungameid/{(FX.EMU_APPID << 32) | 0x02000000}"


@test
def L02_artwork_both_cache_layouts_grid_and_no_traversal():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"))
        assert library.art_path(root, "1001", "capsule").endswith("1001_library_600x900.jpg")
        assert library.art_path(root, "1002", "hero").endswith("1002/library_hero.jpg")
        assert library.art_path(root, "1002", "capsule").endswith("1002/a1b2c3/library_600x900.jpg")
        assert library.art_path(root, str(FX.EMU_APPID), "capsule").endswith(f"grid/{FX.EMU_APPID}p.png")
        assert library.art_path(root, "1003", "capsule") is None
        for appid, kind in (("../1001", "capsule"), ("1001", "../../etc/passwd"), ("", "hero"), ("0", "hero"),
                            ("1001/..", "hero"), ("12345678901", "hero")):
            assert library.art_path(root, appid, kind) is None, (appid, kind)


@test
def L03_missing_or_damaged_steam_files_never_crash():
    with Tmp() as d:
        assert library.scan(os.path.join(d, "nothing")) == {"root_found": False, "games": []}
        root = FX.build(os.path.join(d, "Steam"))
        with open(os.path.join(root, "steamapps", "appmanifest_1001.acf"), "w") as f:
            f.write('"AppState" { "appid" "1001" ')
        with open(os.path.join(root, "userdata", FX.ACCOUNT, "config", "localconfig.vdf"), "w") as f:
            f.write("garbage {")
        names = [g["name"] for g in library.scan(root)["games"]]
        assert "Marea Lenta" not in names and "Basalto <b>&</b>" in names


# ---------------------------------------------------------------- shortcut
@test
def K01_shortcut_id_formula_is_stable():
    a = SC.short_appid()
    assert a & 0x80000000 and a < 2**32
    assert SC.game_id(a) == (a << 32) | 0x02000000
    assert SC.launch_url() == f"steam://rungameid/{SC.game_id(a)}"


@test
def K02_register_adds_once_keeps_user_shortcuts_and_backs_up():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"), with_stone_shortcut=False)
        os.makedirs(os.path.join(root, "userdata", "0"))                 # anonymous: ignored
        proc = os.path.join(d, "proc")
        os.makedirs(proc)
        os.environ["STONE_GRID_DIR"] = os.path.join(d, "grid-src")
        os.makedirs(os.environ["STONE_GRID_DIR"])
        with open(os.path.join(os.environ["STONE_GRID_DIR"], "capsule.png"), "wb") as f:
            f.write(FX.png(6, 9, (0, 0, 0)))
        r1 = SC.register(root, proc=proc)
        assert r1["accounts"] == {FX.ACCOUNT: "added"}, r1
        r2 = SC.register(root, proc=proc)
        assert r2["accounts"] == {FX.ACCOUNT: "unchanged"}, r2
        path = os.path.join(root, "userdata", FX.ACCOUNT, "config", "shortcuts.vdf")
        items = vdf.binary_loads(open(path, "rb").read())["shortcuts"]
        assert sorted(items) == ["0", "1"]
        assert items["0"]["AppName"] == "Órbita Norte"
        assert items["1"]["appid"] == SC.short_appid() and items["1"]["Exe"] == SC.EXE
        assert os.path.exists(path + ".stone-backup")
        assert os.path.exists(os.path.join(root, "userdata", FX.ACCOUNT, "config", "grid", f"{SC.short_appid()}p.png"))
        assert SC.is_registered(root)
        del os.environ["STONE_GRID_DIR"]


@test
def K08_register_keeps_the_id_steam_gave_and_launch_tries_the_real_ids_first():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"), with_stone_shortcut=False)
        proc = os.path.join(d, "proc")
        os.makedirs(proc)
        os.environ["STONE_HOME_STATE"] = os.path.join(d, "state")
        try:
            SC.register(root, proc=proc)
            path = os.path.join(root, "userdata", FX.ACCOUNT, "config", "shortcuts.vdf")
            data = vdf.binary_loads(open(path, "rb").read())
            steam_id = 0x8ABCDEF1                       # Steam reassigned the shortcut's id
            data["shortcuts"]["1"]["appid"] = steam_id
            with open(path, "wb") as f:
                f.write(vdf.binary_dumps(data))
            assert SC.register(root, proc=proc)["accounts"] == {FX.ACCOUNT: "unchanged"}
            assert vdf.binary_loads(open(path, "rb").read())["shortcuts"]["1"]["appid"] == steam_id
            assert SC.shortcut_appids(root) == [steam_id]
            assert SC.candidate_game_ids(root) == [SC.game_id(steam_id), SC.game_id()]
            # STONE Home started by Steam: env and the reaper above it give the id really used
            os.makedirs(os.path.join(proc, "10"))
            os.makedirs(os.path.join(proc, "20"))
            with open(os.path.join(proc, "10", "cmdline"), "wb") as f:
                f.write(b"/home/u/.local/share/Steam/ubuntu12_32/reaper\0SteamLaunch\0AppId=2999999999\0--\0")
            with open(os.path.join(proc, "10", "stat"), "w") as f:
                f.write("10 (reaper) S 1 1 1")
            with open(os.path.join(proc, "20", "cmdline"), "wb") as f:
                f.write(b"/bin/sh\0/usr/bin/stone-home\0")
            with open(os.path.join(proc, "20", "stat"), "w") as f:
                f.write("20 (sh) S 10 1 1")
            rec = SC.record_ids(env={"SteamGameId": str(SC.game_id(2999999999)), "SteamAppId": "x"}, pid=20, proc=proc)
            assert rec == {"SteamGameId": SC.game_id(2999999999), "ReaperAppId": 2999999999}, rec
            assert SC.recorded_ids() == rec
            assert SC.candidate_game_ids(root) == [SC.game_id(2999999999), SC.game_id(steam_id), SC.game_id()]
            assert SC.record_ids(env={}, pid=99999, proc=proc) is None          # not started by Steam
        finally:
            del os.environ["STONE_HOME_STATE"]


@test
def K09_launch_request_carries_steams_display():
    from stone_home import session
    with Tmp() as d:
        proc = os.path.join(d, "proc")
        os.makedirs(os.path.join(proc, "77"))
        with open(os.path.join(proc, "77", "comm"), "w") as f:
            f.write("steam\n")
        with open(os.path.join(proc, "77", "environ"), "wb") as f:
            f.write(b"DISPLAY=:0\0LD_PRELOAD=/x.so\0XDG_CURRENT_DESKTOP=gamescope\0")
        env = session._steam_env(proc)
        assert env["DISPLAY"] == ":0" and env["XDG_CURRENT_DESKTOP"] == "gamescope"
        assert env.get("LD_PRELOAD") == os.environ.get("LD_PRELOAD")      # only display variables copied


@test
def K10_steam_ui_launch_runs_rungame_in_steams_page():
    """steamcef against a fake Steam remote-debugging endpoint (HTTP /json + WebSocket)."""
    import base64 as b64
    import hashlib as hl
    import socket
    import struct
    import threading
    from stone_home import steamcef
    got = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]

    def recv_frame(c):
        b0, b1 = c.recv(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", c.recv(2))[0]
        mask = c.recv(4)
        data = b""
        while len(data) < n:
            data += c.recv(n - len(data))
        return bytes(x ^ mask[i % 4] for i, x in enumerate(data)).decode()

    def send_frame(c, text):
        d = text.encode()
        c.sendall(bytes([0x81, 126]) + struct.pack(">H", len(d)) + d if len(d) >= 126 else bytes([0x81, len(d)]) + d)

    def serve():
        for _ in range(2):
            c, _a = srv.accept()
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(1024)
            line = req.split(b"\r\n", 1)[0].decode()
            if line.startswith("GET /json"):
                body = json.dumps([
                    {"title": "QuickAccess_uid2", "url": "https://steamloopback.host/x", "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/QA"},
                    {"title": "SharedJSContext", "url": "https://steamloopback.host/index.html", "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/SJS"},
                ]).encode()
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
            else:
                got.append(line)
                key = [x.split(b": ", 1)[1] for x in req.split(b"\r\n") if x.lower().startswith(b"sec-websocket-key")][0]
                acc = b64.b64encode(hl.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
                c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
                msg = json.loads(recv_frame(c))
                got.append(msg)
                send_frame(c, json.dumps({"method": "Runtime.consoleAPICalled", "params": {}}))   # an event first
                send_frame(c, json.dumps({"id": msg["id"], "result": {"result": {"type": "string", "value": "ok"}}}))
            c.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    st = steamcef.run_game(SC.game_id(), port=port)
    t.join(5)
    srv.close()
    assert st == "cef[SharedJSContext]: ok", st
    assert got[0].startswith("GET /devtools/page/SJS ")
    expr = got[1]["params"]["expression"]
    assert got[1]["method"] == "Runtime.evaluate" and f"RunGame('{SC.game_id()}','',-1,100)" in expr, expr
    assert steamcef.run_game(1, port=1).startswith("cef unavailable")
    try:
        steamcef.evaluate("ws://192.0.2.1:8080/devtools/page/x", "1")
        raise AssertionError("non-local debugger url accepted")
    except ValueError:
        pass
    try:
        steamcef.run_game_expression("1');alert('x")
        raise AssertionError("non-numeric game id accepted")
    except ValueError:
        pass
    with Tmp() as d:
        assert steamcef.enable(d) and os.path.exists(os.path.join(d, steamcef.FLAG))


@test
def K03_register_never_writes_while_steam_runs_or_over_unreadable_file():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"), with_stone_shortcut=False)
        proc = os.path.join(d, "proc")
        os.makedirs(os.path.join(proc, "4242"))
        with open(os.path.join(proc, "4242", "comm"), "w") as f:
            f.write("steamwebhelper\n")
        path = os.path.join(root, "userdata", FX.ACCOUNT, "config", "shortcuts.vdf")
        before = open(path, "rb").read()
        assert SC.register(root, proc=proc)["result"] == "skipped"
        assert open(path, "rb").read() == before
        with open(path, "wb") as f:
            f.write(b"\x00shortcuts\x00\x00broken")
        shutil.rmtree(os.path.join(proc, "4242"))
        r = SC.register(root, proc=proc)
        assert r["accounts"][FX.ACCOUNT].startswith("skipped: unreadable"), r
        assert open(path, "rb").read() == b"\x00shortcuts\x00\x00broken"


@test
def K04_wait_for_steam_needs_it_alive_for_settle_time():
    with Tmp() as d:
        proc = os.path.join(d, "proc")
        os.makedirs(os.path.join(proc, "7"))
        with open(os.path.join(proc, "7", "comm"), "w") as f:
            f.write("steam\n")
        t = [0.0]
        tick = lambda s: t.__setitem__(0, t[0] + s)  # noqa: E731
        waited = SC.wait_for_steam(timeout_s=60, settle_s=2, proc=proc, sleep=tick, clock=lambda: t[0])
        assert waited is not None and 2 <= waited < 3, waited          # 2 s settle, not the old 10 s
        shutil.rmtree(os.path.join(proc, "7"))
        t[0] = 0.0
        assert SC.wait_for_steam(timeout_s=20, settle_s=2, proc=proc, sleep=tick, clock=lambda: t[0]) is None


@test
def K06_launch_repeats_until_stone_home_really_runs():
    with Tmp() as d:
        proc = os.path.join(d, "proc")
        os.makedirs(proc)
        t, sent = [0.0], []
        tick = lambda s: t.__setitem__(0, t[0] + s)  # noqa: E731

        def request():                      # Steam drops the first two requests (UI still loading)
            sent.append(t[0])
            if len(sent) == 3:
                os.makedirs(os.path.join(proc, "900"))
                with open(os.path.join(proc, "900", "cmdline"), "wb") as f:
                    f.write(b"/usr/bin/python3\0-I\0/usr/libexec/stone-home\0")
        r = SC.launch_until_running(request, timeout_s=60, retry_s=3, proc=proc, sleep=tick, clock=lambda: t[0])
        assert r["ok"] and r["requests"] == 3 and 6 <= r["seconds"] < 7, r
        r2 = SC.launch_until_running(lambda: sent.append("again"), proc=proc, sleep=tick, clock=lambda: t[0])
        assert r2 == {"ok": True, "requests": 0, "seconds": 0.0} and "again" not in sent   # already running: nothing sent
        shutil.rmtree(os.path.join(proc, "900"))
        t[0] = 0.0
        r3 = SC.launch_until_running(lambda: None, timeout_s=10, retry_s=3, proc=proc, sleep=tick, clock=lambda: t[0])
        assert not r3["ok"] and r3["requests"] == 4, r3


@test
def K07_startup_movie_installed_updated_never_replaces_the_users():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"))
        src = os.path.join(d, "stone.webm")
        with open(src, "wb") as f:
            f.write(b"stone-v1")
        dest = os.path.join(root, "config", "uioverrides", "movies", "deck_startup.webm")
        assert SC.install_boot_video(os.path.join(d, "nosteam"), src).startswith("skipped")
        assert SC.install_boot_video(root, src) == "installed" and open(dest, "rb").read() == b"stone-v1"
        assert SC.install_boot_video(root, src) == "unchanged"
        with open(src, "wb") as f:
            f.write(b"stone-v2")
        assert SC.install_boot_video(root, src) == "installed" and open(dest, "rb").read() == b"stone-v2"
        with open(dest, "wb") as f:                     # the user puts their own movie there
            f.write(b"my-own-movie")
        assert SC.install_boot_video(root, src) == "kept: the user's own startup movie"
        assert open(dest, "rb").read() == b"my-own-movie"
        os.remove(dest + ".stone")
        os.remove(dest)
        with open(dest, "wb") as f:                     # a movie that was there before STONE
            f.write(b"older-movie")
        assert SC.install_boot_video(root, src, system_dirs=()).startswith("kept") and open(dest, "rb").read() == b"older-movie"
        assert not os.path.exists(dest + ".stone-tmp")
        # A copy of a movie the system image ships (Bazzite's, first boot on the board) is
        # replaced, and kept aside; the user's own still is not.
        sysdir = os.path.join(d, "usr-share", "bazzite")
        os.makedirs(sysdir)
        with open(os.path.join(sysdir, "deck_startup.webm"), "wb") as f:
            f.write(b"older-movie")
        assert SC.install_boot_video(root, src, system_dirs=(os.path.join(d, "usr-share"),)) == "installed"
        assert open(dest, "rb").read() == b"stone-v2" and open(dest + ".stone-replaced", "rb").read() == b"older-movie"
        with open(dest, "wb") as f:
            f.write(b"my-own-movie")
        assert SC.install_boot_video(root, src, system_dirs=(os.path.join(d, "usr-share"),)).startswith("kept")


@test
def K05_stone_home_is_not_a_game_for_stoned():
    a = SC.short_appid()
    assert games.parse_reaper(["/home/u/.local/share/Steam/ubuntu12_32/reaper", "SteamLaunch", f"AppId={a}", "--",
                               "/home/u/.local/share/Steam/ubuntu12_32/steam-launch-wrapper", "--", '"/usr/bin/stone-home"']) is None
    assert games.parse_reaper(["/x/reaper", "SteamLaunch", "AppId=1001", "--", "/x/steam-launch-wrapper", "--", "/games/MareaLenta"]) == "1001"
    assert games.parse_reaper(["/x/reaper", "SteamLaunch", f"AppId={FX.EMU_APPID}", "--", "/usr/bin/flatpak", "run", "org.example.Emu"]) == str(FX.EMU_APPID)


# ---------------------------------------------------------------- settings and system
@test
def P01_prefs_are_validated_and_defaulted():
    with Tmp() as d:
        os.environ["XDG_CONFIG_HOME"] = d
        assert system.load_prefs()["sound"] is True
        p = system.save_prefs({"sound": False, "vol": 7, "ts": 9, "idle": 1, "favorites": ["1001", "x", "../a"], "evil": 1})
        assert p["sound"] is False and p["vol"] == 0.7 and p["ts"] == 0 and p["idle"] == 1
        assert p["favorites"] == ["1001"] and "evil" not in p
        assert system.load_prefs() == p
        with open(system.prefs_path(), "w") as f:
            f.write("{broken")
        assert system.load_prefs()["vol"] == 0.7
        del os.environ["XDG_CONFIG_HOME"]


@test
def P02_only_known_steam_urls_can_be_opened():
    seen = []
    s = system.System(spawn=seen.append)
    s.open_url("steam://rungameid/1001")
    s.open_url(system.STEAM_URLS["store"])
    for bad in ("steam://rungameid/1001;rm", "steam://install/1", "https://example.com", "file:///etc/passwd",
                "steam://rungameid/", "steam://rungameid/12 3"):
        try:
            s.open_url(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    assert seen == ["steam://rungameid/1001", "steam://url/StoreFrontPage"]


@test
def P03_release_file_and_rpm_ostree_status():
    with Tmp() as d:
        p = os.path.join(d, "rel")
        with open(p, "w") as f:
            f.write('STONE_VERSION="0.1.0-rc1"\nSTONE_COMMIT="abc"\nOTHER=1\n')
        assert system.read_release(p) == {"STONE_VERSION": "0.1.0-rc1", "STONE_COMMIT": "abc"}

    class R:
        stdout = json.dumps({"deployments": [
            {"staged": True, "booted": False, "version": "0.1.0-rc2", "container-image-reference": "x:rc"},
            {"booted": True, "version": "0.1.0-rc1", "container-image-reference": "x:rc"},
            {"booted": False, "version": "0.1.0-beta", "container-image-reference": "x:rc"}]})
    dep = system.deployments(run=lambda *a, **k: R)
    assert dep["staged"]["version"] == "0.1.0-rc2" and dep["booted"]["version"] == "0.1.0-rc1"
    assert dep["rollback"]["version"] == "0.1.0-beta"
    assert system.deployments(run=lambda *a, **k: (_ for _ in ()).throw(OSError("no"))) is None


# ---------------------------------------------------------------- host request table
class FakeStoned:
    def __init__(self):
        self.calls = []

    def call(self, method, args, done):
        self.calls.append((method, args))
        done({"profile": "balanced"})


def run_host(h, req, timeout=5):
    box = {}
    loop = GLib.MainLoop()

    def done(result=None, code=None, message=""):
        box["r"] = (result, code)
        loop.quit()
    GLib.idle_add(lambda: (h.handle(req, done), False)[1])
    GLib.timeout_add_seconds(timeout, loop.quit)
    loop.run()
    return box.get("r")


@test
def H01_host_dispatches_only_known_methods():
    seen = []
    h = host_mod.Host(lambda *a: None, stoned=FakeStoned(), sysapi=system.System(spawn=seen.append), steam_root="/nonexistent")
    for bad in ({"method": "__init__"}, {"method": "m_power"}, {"method": "perf"}, {"method": "library.x"},
                {"method": "os.system", "args": ["id"]}, None, {"method": 3}, {"method": "launch", "args": "1001"},
                {"method": "launch", "args": [1, 2, 3, 4, 5]}):
        r = run_host(h, bad)
        assert r and r[1] == "InvalidValue", (bad, r)
    assert run_host(h, {"method": "perf.getState", "args": []}) == ({"profile": "balanced"}, None)
    assert h.stoned.calls == [("getState", [])]


@test
def H02_launch_only_games_from_the_last_library_scan():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"))
        seen = []
        h = host_mod.Host(lambda *a: None, stoned=FakeStoned(), sysapi=system.System(spawn=seen.append), steam_root=root)
        h.steam_ui = FakeSteamUI(ok=False)                 # Steam's interface not reachable: URL fallback
        assert run_host(h, {"method": "launch", "args": ["1001"]})[1] == "InvalidValue"   # no scan yet
        lib, code = run_host(h, {"method": "library", "args": []})
        assert code is None and len(lib["games"]) == 4
        assert run_host(h, {"method": "launch", "args": ["1001"]}) == ({"ok": True, "via": "url"}, None)
        assert run_host(h, {"method": "launch", "args": [str(FX.EMU_APPID)]})[1] is None
        assert run_host(h, {"method": "launch", "args": ["228980"]})[1] == "InvalidValue"
        assert run_host(h, {"method": "openSteam", "args": ["store"]})[1] is None
        assert run_host(h, {"method": "openSteam", "args": ["steam://install/1"]})[1] == "InvalidValue"
        assert seen == ["steam://rungameid/1001", f"steam://rungameid/{(FX.EMU_APPID << 32) | 0x02000000}",
                        "steam://url/StoreFrontPage"]


class FakeSteamUI:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def _r(self, what):
        self.calls.append(what)
        return "cef[SharedJSContext]: ok" if self.ok else "cef unavailable (URLError)"

    def run_game(self, gid):
        return self._r(("run", gid))

    def open_settings(self, sec):
        return self._r(("settings", sec))

    def open_page(self, page):
        return self._r(("page", page))

    def owned_games(self):
        return None                                        # Steam's interface not asked in these tests


@test
def H04_steam_ui_first_for_games_and_steam_settings_only_known_sections():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"))
        seen = []
        h = host_mod.Host(lambda *a: None, stoned=FakeStoned(), sysapi=system.System(spawn=seen.append), steam_root=root)
        ui = h.steam_ui = FakeSteamUI(ok=True)
        run_host(h, {"method": "library", "args": []})
        assert run_host(h, {"method": "launch", "args": ["1001"]}) == ({"ok": True, "via": "steam-ui"}, None)
        assert run_host(h, {"method": "openSteamSettings", "args": ["Display"]}) == ({"ok": True, "via": "steam-ui"}, None)
        assert run_host(h, {"method": "openSteamSettings", "args": ["Display');x('"]})[1] == "InvalidValue"
        assert run_host(h, {"method": "openSteam", "args": ["downloads"]})[0]["via"] == "steam-ui"
        assert ui.calls == [("run", 1001), ("settings", "Display"), ("page", "Downloads")] and seen == []
        ui.ok = False                                      # settings have no URL fallback: say where instead
        assert run_host(h, {"method": "openSteamSettings", "args": ["Audio"]})[1] == "Unsupported"
        assert run_host(h, {"method": "restart", "args": []})[1] == "Unsupported"     # not wired outside app.py
    from stone_home import steamcef
    assert "n.Settings('Display')" in steamcef.open_settings_expression("Display")
    for bad in ("display", "Display'", ""):
        try:
            steamcef.open_settings_expression(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert system.clean_prefs({"render": 2})["render"] == 2 and system.clean_prefs({"render": 7})["render"] == 0


@test
def H05_library_lists_account_games_not_installed_and_installs_through_steam():
    with Tmp() as d:
        root = FX.build(os.path.join(d, "Steam"))
        h = host_mod.Host(lambda *a: None, stoned=FakeStoned(), sysapi=system.System(spawn=lambda u: None), steam_root=root)
        ui = h.steam_ui = FakeSteamUI(ok=True)
        ui.owned_games = lambda: [(1001, "Ya instalado", True, 5, 10, 1), (35140, "Batman: Arkham Asylum GOTY Edition", False, 48, 99, 0),
                                  (77, "", False, 0, 0, 0)]
        ui.install = lambda appid: ui._r(("install", appid))
        lib, code = run_host(h, {"method": "library", "args": []})
        names = [g["name"] for g in lib["games"]]
        assert code is None and lib["owned_known"] and "Batman: Arkham Asylum GOTY Edition" in names and "Ya instalado" not in names
        batman = [g for g in lib["games"] if g["appid"] == "35140"][0]
        assert batman["installed"] is False and batman["launch"] is None and batman["playtime_min"] == 48
        assert run_host(h, {"method": "launch", "args": ["35140"]})[1] == "InvalidValue"      # not installed: no launch
        assert run_host(h, {"method": "install", "args": ["35140"]}) == ({"ok": True, "via": "steam-ui"}, None)
        assert run_host(h, {"method": "install", "args": ["1001"]})[1] == "InvalidValue"     # installed: nothing to install
        assert ("install", 35140) in ui.calls
    from stone_home import steamcef
    assert "OpenInstallWizard([35140])" in steamcef.install_expression(35140)
    for bad in (0, -1, 0x80000000):
        try:
            steamcef.install_expression(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


@test
def E01_emulators_fixed_catalog_install_and_shortcut_now_or_at_next_start():
    from stone_home import emulators
    with Tmp() as d:
        os.environ["STONE_HOME_STATE"] = os.path.join(d, "state")
        try:
            calls, events = [], []
            have = set()

            class R:
                def __init__(self, out="", rc=0):
                    self.stdout, self.stderr, self.returncode = out, "", rc

            def run(args, timeout):
                calls.append(args)
                if args[1] == "list":
                    return R("\n".join(sorted(have)) if args[2] == "--user" else "")
                if args[1] == "install":
                    have.add(args[-1])
                return R()

            class UI:
                def __init__(self, ok):
                    self.ok, self.added = ok, []

                def add_shortcut(self, *a):
                    self.added.append(a)
                    return 3000000001 if self.ok else None
            ui = UI(True)
            assert emulators.install("dolphin", lambda st, t: events.append(st), run=run, steam_ui=ui)
            assert ["/usr/bin/flatpak", "install", "--user", "-y", "--noninteractive", "flathub", "org.DolphinEmu.dolphin-emu"] in calls
            assert ui.added == [("Dolphin", "/usr/bin/flatpak", "/", "run org.DolphinEmu.dolphin-emu")] and events == ["installing", "done"]
            rows = {r["key"]: r for r in emulators.catalog(run=run)}
            assert rows["dolphin"]["installed"] and not rows["pcsx2"]["installed"]
            assert not emulators.install("rm -rf /", lambda st, t: events.append(st), run=run)
            # Steam's interface not reachable: queued, written by register before Steam starts
            assert emulators.install("pcsx2", lambda st, t: None, run=run, steam_ui=UI(False))
            root = FX.build(os.path.join(d, "Steam"), with_stone_shortcut=False)
            proc = os.path.join(d, "proc")
            os.makedirs(proc)
            out = SC.register(root, proc=proc)
            assert out["emulators_added"] == ["PCSX2"], out
            items = vdf.binary_loads(open(os.path.join(root, "userdata", FX.ACCOUNT, "config", "shortcuts.vdf"), "rb").read())["shortcuts"]
            pc = [v for v in items.values() if v.get("AppName") == "PCSX2"][0]
            assert pc["Exe"] == '"/usr/bin/flatpak"' and pc["LaunchOptions"] == "run net.pcsx2.PCSX2" and pc["appid"] & 0x80000000
            assert "emulators_added" not in SC.register(root, proc=proc)          # queue emptied, nothing twice
        finally:
            del os.environ["STONE_HOME_STATE"]
    from stone_home import steamcef
    try:
        steamcef.add_shortcut_expression("x'); evil('", "/usr/bin/flatpak", "/", "run a")
        raise AssertionError("unsafe shortcut accepted")
    except ValueError:
        pass


@test
def H03_bridge_rejects_bad_arguments_before_dbus():
    st = bridge.Stoned.__new__(bridge.Stoned)          # no bus needed: validation fails first
    out = []
    for m, a in (("setProfile", ["turbo"]), ("setProfile", [1]), ("startTest", [5, {"max": 1900, "min": 1000, "temp": 80}, "quick"]),
                 ("startTest", [0, {"max": "1900", "min": 1000, "temp": 80}, "quick"]),
                 ("startTest", [0, {"max": 1900, "min": 1000, "temp": 80, "volt": 1}, "quick"]),
                 ("startTest", [0, {"max": 1900, "min": 1000, "temp": 80}, "forever"]), ("setGameProfile", ["1001;x", "c1"]),
                 ("subscribe", ["everything"]), ("SetProfile", ["silent"]), ("getState", ["extra"]), ("cancelTest", "x")):
        st.call(m, a, lambda r=None, code=None, msg="": out.append(code))
    assert out == ["InvalidValue"] * 11, out


# ---------------------------------------------------------------- bridge <-> real stoned
@test
def B01_bridge_talks_to_real_stoned_and_reads_back_actual_state():
    import run_tests as RT
    env = RT.Env()
    try:
        RT.R.recover(env.store())
        env.start_stoned()
        from gi.repository import Gio
        events = []
        stn = bridge.Stoned(lambda n, d: events.append((n, d)), bus=Gio.DBusConnection.new_for_address_sync(
            env.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None))
        loop = GLib.MainLoop()

        def call(m, *a):
            box = {}

            def done(result=None, code=None, msg=""):
                box["r"] = (result, code)
                loop.quit()
            stn.call(m, list(a), done)
            GLib.timeout_add_seconds(10, loop.quit)
            loop.run()
            return box["r"]

        r, code = call("homeReady")
        assert code is None and r["profile"] == "balanced"
        r, code = call("setProfile", "performance")
        assert code is None and r["actual"]["max"] == 2000, r            # read back from the governor
        assert env.gov_state()["max"] == 2000
        deadline = time.time() + 3
        while not any(n == "state" for n, _ in events) and time.time() < deadline:
            GLib.MainContext.default().iteration(False)
        assert any(n == "state" and d["profile"] == "performance" for n, d in events), events
        r, code = call("startTest", 1, {"max": 1900, "min": 1000, "temp": 90}, "quick")
        assert code == "InvalidValue", (r, code)                          # stoned's own range check
        env.stop_governor()
        r, code = call("setProfile", "silent")
        assert code in ("ServiceUnavailable", "Unsupported", "HardwareError"), code
        env.kill_stoned()
        r, code = call("getState")
        assert code == "ServiceUnavailable", code
        deadline = time.time() + 3
        while not any(n == "service" and not d["up"] for n, d in events) and time.time() < deadline:
            GLib.MainContext.default().iteration(False)
        assert any(n == "service" and d == {"up": False} for n, d in events)
    finally:
        env.close()


def main():
    only = sys.argv[1:]
    passed = failed = 0
    for fn in TESTS:
        if only and not any(o in fn.__name__ for o in only):
            continue
        t0 = time.time()
        try:
            fn()
            passed += 1
            print(f"PASS  {fn.__name__}  ({time.time() - t0:.1f}s)", flush=True)
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}", flush=True)
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
