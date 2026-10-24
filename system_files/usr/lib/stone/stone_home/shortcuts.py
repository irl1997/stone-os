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

The shortcut id is fixed and written explicitly ("appid"), so the id used by `launch`
is the one Steam uses. Formula used by Steam for shortcut ids:
crc32(exe + name) | 0x80000000; the game id for steam:// URLs is (id << 32) | 0x02000000.
"""

import binascii
import hashlib
import os
import shutil
import tempfile
import time

from . import vdf

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
        for k in ("appid", "AppName", "Exe", "StartDir", "icon", "AllowOverlay", "IsHidden"):
            merged[k] = want[k]
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
    _install_grid(cfg, appid)
    return result


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
    for a in accts:
        try:
            out["accounts"][os.path.basename(a)] = register_account(a)
        except OSError as e:
            out["accounts"][os.path.basename(a)] = f"skipped: {e}"
    return out


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
# STONE only overwrites a file it installed itself (recorded in the .stone marker).
BOOT_VIDEO = "/usr/share/stone-home/boot/stone-startup.webm"
BOOT_VIDEO_NAME = "deck_startup.webm"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install_boot_video(root, src=None):
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
        if mine != have:
            return "kept: the user's own startup movie"
        if have == want:
            return "unchanged"
    os.makedirs(movies, exist_ok=True)
    tmp = dest + ".stone-tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    with open(marker, "w") as f:
        f.write(want + "\n")
    return "installed"


def launch_url():
    return f"steam://rungameid/{game_id()}"
