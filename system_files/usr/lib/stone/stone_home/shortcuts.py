"""STONE Home as a non-Steam shortcut, and its start when Game Mode comes up.

Steam Game Mode is the session (architecture decision 2): STONE Home runs inside it as a
non-Steam shortcut, so Steam and Gamescope give it focus, input and overlay like any game.

  register  Before Steam starts (user unit ordered before the Game Mode session), add or
            update the STONE shortcut in every Steam account's shortcuts.vdf. Steam
            rewrites that file when it exits, so it is never edited while Steam runs.
  launch    As soon as Steam's UI process is up, ask it to open the shortcut with
            steam://rungameid/<id>, repeating the request until STONE Home is running
            (launch_until_running). If this fails, Steam's own Home stays on screen.
  movie     Steam's startup movie becomes STONE's (install_boot_video), so the boot line
            continues from Plymouth through Steam's loading.

Shortcut id: a new shortcut is written with crc32(exe + name) | 0x80000000, but Steam may
assign its own id to a non-Steam shortcut (ValveSoftware/steam-for-linux#9463), and on the
board (RC2) Steam ignored steam://rungameid for the computed id. So the id Steam really uses
is never overwritten once present, and `launch` tries, in order: the ids STONE Home saw when
Steam started it (SteamGameId / reaper AppId=, saved by record_ids), the id in shortcuts.vdf,
and the computed one. The game id for steam:// URLs is (id << 32) | 0x02000000.
"""

import binascii
import hashlib
import json
import os
import shutil
import tempfile
import time

from . import steamcef, vdf

NAME = "STONE"
EXE = '"/usr/bin/stone-home"'
START_DIR = '"/usr/bin/"'
ICON = "/usr/share/stone-home/icon.png"
GRID_DIR = "/usr/share/stone-home/grid"
MARK = "/usr/bin/stone-home"
HOME_PROCESS = b"/usr/libexec/stone-home"


def short_appid(exe=EXE, name=NAME):
    return (binascii.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def game_id(appid=None):
    appid = short_appid() if appid is None else appid
    return (appid << 32) | 0x02000000


def steam_root(home=None):
    if os.environ.get("STONE_STEAM_ROOT"):            # tests and development only
        return os.environ["STONE_STEAM_ROOT"]
    home = home or os.path.expanduser("~")
    for c in (os.path.join(home, ".local/share/Steam"), os.path.join(home, ".steam/steam")):
        if os.path.isdir(c):
            return os.path.realpath(c)
    return os.path.join(home, ".local/share/Steam")


def accounts(root):
    base = os.path.join(root, "userdata")
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return sorted(os.path.join(base, n) for n in names if n.isdigit() and n != "0"
                  and os.path.isdir(os.path.join(base, n)))


def steam_running(proc="/proc"):
    """True if a Steam client process is alive (the file must not be edited then)."""
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return False
    for p in pids:
        try:
            with open(os.path.join(proc, p, "comm")) as f:
                comm = f.read().strip()
        except OSError:
            continue
        if comm in ("steam", "steamwebhelper"):
            return True
    return False


def entry(appid):
    return {
        "appid": appid, "AppName": NAME, "Exe": EXE, "StartDir": START_DIR, "icon": ICON,
        "ShortcutPath": "", "LaunchOptions": "", "IsHidden": 0, "AllowDesktopConfig": 1,
        "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0,
        "LastPlayTime": 0, "FlatpakAppID": "", "tags": {},
    }


def _write_atomic(path, data):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".stone-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register_account(acct, appid=None):
    """Add or update the STONE shortcut in one account. Returns 'added', 'updated',
    'unchanged' or 'skipped: <reason>'."""
    appid = short_appid() if appid is None else appid
    cfg = os.path.join(acct, "config")
    os.makedirs(cfg, exist_ok=True)
    path = os.path.join(cfg, "shortcuts.vdf")
    data = {"shortcuts": {}}
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = vdf.binary_loads(f.read())
        except (OSError, vdf.VDFError) as e:
            return f"skipped: unreadable shortcuts.vdf ({e})"   # never overwrite what we cannot read
        if not isinstance(data.get("shortcuts"), dict):
            return "skipped: unexpected shortcuts.vdf layout"
    items = data["shortcuts"]
    mine = [k for k, v in items.items() if isinstance(v, dict)
            and MARK in str(vdf.get_ci(v, "Exe") or "")]
    want = entry(appid)
    if mine:
        cur = items[mine[0]]
        merged = dict(cur)
        for k in ("AppName", "Exe", "StartDir", "icon", "AllowOverlay", "IsHidden"):
            merged[k] = want[k]
        # The id Steam gave the shortcut is kept: Steam launches it by that id.
        if not _valid_appid(vdf.get_ci(cur, "appid")):
            merged["appid"] = want["appid"]
        for extra in mine[1:]:
            del items[extra]
        if merged == cur and len(mine) == 1:
            result = "unchanged"
        else:
            items[mine[0]] = merged
            result = "updated"
    else:
        idx = max([int(k) for k in items if k.isdigit()] + [-1]) + 1
        items[str(idx)] = want
        result = "added"
    # renumber 0..n-1 as Steam expects
    order = sorted(items.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 1 << 30)
    data["shortcuts"] = {str(i): v for i, (_, v) in enumerate(order)}
    if result != "unchanged":
        if os.path.exists(path) and not os.path.exists(path + ".stone-backup"):
            shutil.copy2(path, path + ".stone-backup")
        _write_atomic(path, vdf.binary_dumps(data))
    for a in {appid, *_entry_appids(data)}:
        _install_grid(cfg, a)
    return result


def _valid_appid(v):
    try:
        v = int(v) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return False
    return v != 0


def _entry_appids(data):
    out = []
    for v in (data.get("shortcuts") or {}).values():
        if isinstance(v, dict) and MARK in str(vdf.get_ci(v, "Exe") or ""):
            a = vdf.get_ci(v, "appid")
            if _valid_appid(a):
                out.append(int(a) & 0xFFFFFFFF)
    return out


def shortcut_appids(root=None):
    """The STONE shortcut's id in every account's shortcuts.vdf (as Steam last saved it)."""
    root = root or steam_root()
    out = []
    for a in accounts(root):
        try:
            with open(os.path.join(a, "config", "shortcuts.vdf"), "rb") as f:
                out.extend(_entry_appids(vdf.binary_loads(f.read())))
        except (OSError, vdf.VDFError):
            continue
    return out


# ---- the ids Steam really used ---------------------------------------------------------
def state_dir(home=None):
    """Same place as /usr/bin/stone-home writes home.log."""
    if os.environ.get("STONE_HOME_STATE"):            # tests and development only
        return os.environ["STONE_HOME_STATE"]
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(home or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "stone")


def _ids_file():
    return os.path.join(state_dir(), "steam-ids.json")


def _reaper_appid(pid, proc="/proc"):
    """AppId=<n> on the command line of the Steam reaper above `pid` (Steam's launcher)."""
    for _ in range(12):
        try:
            with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
                args = f.read().split(b"\0")
            with open(os.path.join(proc, str(pid), "stat")) as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if args and args[0].endswith(b"/reaper"):
            for a in args:
                if a.startswith(b"AppId=") and a[6:].isdigit():
                    return int(a[6:])
        if ppid <= 1:
            return None
        pid = ppid
    return None


def record_ids(env=None, pid=None, proc="/proc"):
    """Called by STONE Home when it starts: keep the ids Steam launched it with."""
    env = os.environ if env is None else env
    found = {}
    for k in ("SteamGameId", "SteamAppId"):
        v = env.get(k, "")
        if v.isdigit() and int(v):
            found[k] = int(v)
    r = _reaper_appid(pid or os.getpid(), proc)
    if r:
        found["ReaperAppId"] = r
    if not found:
        return None
    try:
        os.makedirs(state_dir(), exist_ok=True)
        _write_atomic(_ids_file(), (json.dumps(found) + "\n").encode())
    except OSError:
        pass
    return found


def recorded_ids():
    try:
        with open(_ids_file()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def candidate_game_ids(root=None):
    """Game ids to try with steam://rungameid, most trustworthy first, without repeats."""
    rec = recorded_ids()
    out = []

    def add(gid):
        if isinstance(gid, int) and gid > 0 and gid not in out:
            out.append(gid)
    g = rec.get("SteamGameId")
    if isinstance(g, int) and g > 0xFFFFFFFF:
        add(g)
    for k in ("ReaperAppId", "SteamAppId", "SteamGameId"):
        v = rec.get(k)
        if isinstance(v, int) and 0 < v <= 0xFFFFFFFF:
            add(game_id(v))
    for a in shortcut_appids(root):
        add(game_id(a))
    add(game_id())
    return out


def _install_grid(cfg, appid):
    """Steam library artwork for the shortcut (capsule, hero, logo, header)."""
    src = os.environ.get("STONE_GRID_DIR", GRID_DIR)
    if not os.path.isdir(src):
        return
    grid = os.path.join(cfg, "grid")
    os.makedirs(grid, exist_ok=True)
    for name, suffix in (("capsule.png", "p.png"), ("hero.png", "_hero.png"), ("logo.png", "_logo.png"),
                         ("header.png", ".png")):
        s = os.path.join(src, name)
        d = os.path.join(grid, f"{appid}{suffix}")
        if os.path.exists(s) and not os.path.exists(d):
            shutil.copyfile(s, d)


def register(root=None, proc="/proc"):
    root = root or steam_root()
    if steam_running(proc):
        return {"result": "skipped", "reason": "steam is running"}
    try:
        video = install_boot_video(root)
    except OSError as e:
        video = f"skipped: {e}"
    accts = accounts(root)
    if not accts:
        return {"result": "skipped", "reason": "no Steam account yet (first sign-in pending)", "startup_movie": video}
    out = {"result": "ok", "appid": short_appid(), "startup_movie": video, "accounts": {}}
    if os.environ.get("STONE_HOME_CEF", "1") != "0":
        # Steam reads this flag when it starts; it lets `launch` start STONE through Steam's
        # interface (steamcef.py), the only way that worked for non-Steam shortcuts on the board.
        out["steam_ui_launch"] = steamcef.enable(root)
    for a in accts:
        try:
            out["accounts"][os.path.basename(a)] = register_account(a)
        except OSError as e:
            out["accounts"][os.path.basename(a)] = f"skipped: {e}"
    # Emulators installed while Steam was running and Steam's interface could not add them.
    from . import emulators
    extra = emulators.pending_entries()
    if extra:
        added = []
        for a in accts:
            try:
                added += add_extra_shortcuts(a, extra)
            except (OSError, vdf.VDFError):
                pass
        out["emulators_added"] = sorted(set(added))
    return out


def add_extra_shortcuts(acct, entries):
    """Non-Steam shortcuts for installed emulators ({name, exe, start, options}); an entry with
    the same Exe and launch options already present is left alone. Steam must be closed."""
    path = os.path.join(acct, "config", "shortcuts.vdf")
    data = {"shortcuts": {}}
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = vdf.binary_loads(f.read())
        if not isinstance(data.get("shortcuts"), dict):
            return []
    items = data["shortcuts"]
    have = {(str(vdf.get_ci(v, "Exe") or ""), str(vdf.get_ci(v, "LaunchOptions") or ""))
            for v in items.values() if isinstance(v, dict)}
    added = []
    for e in entries:
        if (e["exe"], e["options"]) in have:
            continue
        appid = (binascii.crc32((e["exe"] + e["name"]).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000
        ent = entry(appid)
        ent.update({"AppName": e["name"], "Exe": e["exe"], "StartDir": e["start"], "icon": "", "LaunchOptions": e["options"]})
        idx = max([int(k) for k in items if k.isdigit()] + [-1]) + 1
        items[str(idx)] = ent
        added.append(e["name"])
    if added:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and not os.path.exists(path + ".stone-backup"):
            shutil.copy2(path, path + ".stone-backup")
        _write_atomic(path, vdf.binary_dumps(data))
    return added


def is_registered(root=None):
    root = root or steam_root()
    for a in accounts(root):
        p = os.path.join(a, "config", "shortcuts.vdf")
        try:
            with open(p, "rb") as f:
                d = vdf.binary_loads(f.read())
        except (OSError, vdf.VDFError):
            continue
        for v in (d.get("shortcuts") or {}).values():
            if isinstance(v, dict) and MARK in str(vdf.get_ci(v, "Exe") or ""):
                return True
    return False


def wait_for_steam(timeout_s=180, settle_s=2, proc="/proc", sleep=time.sleep, clock=time.monotonic, poll_s=0.5):
    """Steam's client is considered up once steamwebhelper (its UI process) has been alive
    for settle_s seconds. Returns the seconds waited, or None on timeout."""
    t0 = clock()
    seen = None
    while clock() - t0 < timeout_s:
        if steam_running(proc):
            seen = seen if seen is not None else clock()
            if clock() - seen >= settle_s:
                return clock() - t0
        else:
            seen = None
        sleep(poll_s)
    return None


def home_running(proc="/proc"):
    """True once the STONE Home process exists (Steam actually started the shortcut)."""
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return False
    for p in pids:
        try:
            with open(os.path.join(proc, p, "cmdline"), "rb") as f:
                if HOME_PROCESS in f.read(512):
                    return True
        except OSError:
            continue
    return False


def launch_until_running(request, timeout_s=120, retry_s=3.0, proc="/proc", sleep=time.sleep,
                         clock=time.monotonic, poll_s=0.25):
    """Ask Steam to open STONE Home and confirm it really started.

    A request sent while Steam's UI is still loading can be dropped, so instead of a long
    fixed wait the request is repeated every retry_s seconds until the STONE Home process
    exists. Once it runs, nothing more is sent (a repeated request for a running shortcut
    only brings it to the front). Returns {"ok", "requests", "seconds"}."""
    t0 = clock()
    requests = 0
    while clock() - t0 < timeout_s:
        if home_running(proc):
            return {"ok": True, "requests": requests, "seconds": round(clock() - t0, 2)}
        request()
        requests += 1
        deadline = clock() + retry_s
        while clock() < deadline:
            if home_running(proc):
                return {"ok": True, "requests": requests, "seconds": round(clock() - t0, 2)}
            sleep(poll_s)
    return {"ok": False, "requests": requests, "seconds": round(clock() - t0, 2)}


# ---- Steam's startup movie --------------------------------------------------------------
# Game Mode plays ~/.steam/root/config/uioverrides/movies/deck_startup.webm, when present,
# instead of its own startup movie. STONE's continues the line of the boot screen, so the
# change from Plymouth to Steam is not seen. A video the user put there is never replaced:
# STONE only overwrites a file it installed itself (recorded in the .stone marker) or a copy
# of a movie shipped by the system image (Bazzite puts its own there; first boot on the
# board), and keeps that one as .stone-replaced.
BOOT_VIDEO = "/usr/share/stone-home/boot/stone-startup.webm"
BOOT_VIDEO_NAME = "deck_startup.webm"
SYSTEM_MOVIE_DIRS = ("/usr/share", "/etc/skel")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _from_system_image(path, dirs):
    """True if `path` is a copy of a .webm shipped read-only by the image (same size and
    SHA-256), i.e. not something the user made."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    want = None
    for top in dirs:
        for d, _sub, files in os.walk(top):
            for n in files:
                if not n.endswith(".webm"):
                    continue
                f = os.path.join(d, n)
                try:
                    if os.path.getsize(f) != size:
                        continue
                    want = want or _sha256(path)
                    if _sha256(f) == want:
                        return True
                except OSError:
                    continue
    return False


def install_boot_video(root, src=None, system_dirs=SYSTEM_MOVIE_DIRS):
    src = src or os.environ.get("STONE_BOOT_VIDEO", BOOT_VIDEO)
    if not os.path.isfile(src):
        return "skipped: no STONE startup movie in the image"
    if not os.path.isdir(root):
        return "skipped: Steam not installed yet"
    movies = os.path.join(root, "config", "uioverrides", "movies")
    dest = os.path.join(movies, BOOT_VIDEO_NAME)
    marker = dest + ".stone"
    want = _sha256(src)
    if os.path.exists(dest):
        try:
            mine = open(marker).read().strip()
        except OSError:
            mine = None
        have = _sha256(dest)
        if have == want and mine == have:
            return "unchanged"
        if mine != have:
            if have == want or not _from_system_image(dest, system_dirs):
                return "kept: the user's own startup movie"
            replaced = dest + ".stone-replaced"
            if not os.path.exists(replaced):
                shutil.copyfile(dest, replaced)
    os.makedirs(movies, exist_ok=True)
    tmp = dest + ".stone-tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    with open(marker, "w") as f:
        f.write(want + "\n")
    return "installed"


def launch_url(gid=None):
    return f"steam://rungameid/{game_id() if gid is None else gid}"


def launch_urls(root=None):
    return [launch_url(g) for g in candidate_game_ids(root)]
