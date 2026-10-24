"""The game library STONE Home shows, read from Steam's own files.

Steam is the launcher of record (architecture decision 2): games installed through Steam,
and non-Steam shortcuts added to Steam (Heroic, emulators and other launchers can add
their games there), appear in STONE Home and are started through Steam.

Read only. Nothing here writes to Steam's files, and nothing takes a path from the UI:
artwork is looked up from a validated AppID and a fixed kind.

Sources
  steamapps/libraryfolders.vdf          library folders
  <library>/steamapps/appmanifest_N.acf installed games (name, state, size, last played)
  userdata/<acct>/config/localconfig.vdf play time and last played per game
  userdata/<acct>/config/shortcuts.vdf   non-Steam shortcuts
  appcache/librarycache                  Steam's cached artwork (old flat layout and the
                                         per-AppID folders of current clients)
  userdata/<acct>/config/grid            custom artwork chosen by the user
"""

import glob
import os
import re

from . import shortcuts as SC
from . import vdf

# Runtimes and tools Steam installs as "apps"; never games.
TOOL_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "2180100", "1161040", "1826330"}
TOOL_NAME = re.compile(r"^(Proton( |$|\b)|Steam Linux Runtime|Steamworks Common|Proton EasyAntiCheat|"
                       r"Proton BattlEye|Steamworks SDK)", re.I)
ART_KINDS = ("capsule", "hero", "logo", "header")
_FLAT = {"capsule": ["{a}_library_600x900.jpg", "{a}_library_600x900.png"],
         "hero": ["{a}_library_hero.jpg", "{a}_library_hero.png"],
         "logo": ["{a}_logo.png"], "header": ["{a}_header.jpg"]}
_DIRS = {"capsule": ["library_600x900.jpg", "library_capsule.jpg", "library_600x900.png"],
         "hero": ["library_hero.jpg", "library_hero.png"],
         "logo": ["logo.png"], "header": ["header.jpg"]}
_GRID = {"capsule": ["{a}p.png", "{a}p.jpg"], "hero": ["{a}_hero.png", "{a}_hero.jpg"],
         "logo": ["{a}_logo.png"], "header": ["{a}.png", "{a}.jpg"]}
MAX_ITEMS = 5000


def valid_appid(a):
    return isinstance(a, str) and a.isdigit() and 0 < len(a) <= 10 and int(a) > 0


def library_paths(root):
    paths = [root]
    for f in (os.path.join(root, "steamapps", "libraryfolders.vdf"), os.path.join(root, "config", "libraryfolders.vdf")):
        try:
            d = vdf.load_file(f)
        except (OSError, vdf.VDFError):
            continue
        lf = vdf.get_ci(d, "libraryfolders") or {}
        for k, v in lf.items():
            p = v.get("path") if isinstance(v, dict) else (v if k.isdigit() else None)
            if isinstance(p, str) and p and os.path.isabs(p):
                paths.append(p)
        break
    seen, out = set(), []
    for p in paths:
        rp = os.path.realpath(p)
        if rp not in seen and os.path.isdir(os.path.join(rp, "steamapps")):
            seen.add(rp)
            out.append(rp)
    return out


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def installed_games(root):
    games = {}
    for lib in library_paths(root):
        for acf in sorted(glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf"))):
            try:
                st = vdf.get_ci(vdf.load_file(acf, limit=2**20), "AppState")
            except (OSError, vdf.VDFError):
                continue
            if not isinstance(st, dict):
                continue
            appid, name = str(vdf.get_ci(st, "appid") or ""), vdf.get_ci(st, "name") or ""
            if not valid_appid(appid) or not name or appid in TOOL_APPIDS or TOOL_NAME.search(name):
                continue
            flags = _int(vdf.get_ci(st, "StateFlags"))
            games[appid] = {
                "appid": appid, "name": name, "source": "steam",
                "installed": bool(flags & 4), "updating": bool(flags & (2 | 1024)),
                "size_bytes": _int(vdf.get_ci(st, "SizeOnDisk")),
                "last_played": _int(vdf.get_ci(st, "LastPlayed")),
                "playtime_min": 0,
            }
    return games


def _accounts_by_recent(root):
    def mt(a):
        try:
            return os.path.getmtime(os.path.join(a, "config", "localconfig.vdf"))
        except OSError:
            return 0
    return sorted(SC.accounts(root), key=mt, reverse=True)


def apply_localconfig(root, games):
    accts = _accounts_by_recent(root)
    if not accts:
        return
    try:
        d = vdf.load_file(os.path.join(accts[0], "config", "localconfig.vdf"), limit=32 * 2**20)
    except (OSError, vdf.VDFError):
        return
    apps = vdf.get_ci(d, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps") \
        or vdf.get_ci(d, "UserLocalConfigStore", "Software", "Valve", "Steam", "Apps") or {}
    for appid, g in games.items():
        a = apps.get(appid) if isinstance(apps, dict) else None
        if isinstance(a, dict):
            g["playtime_min"] = _int(vdf.get_ci(a, "Playtime"))
            g["last_played"] = max(g["last_played"], _int(vdf.get_ci(a, "LastPlayed")))


def shortcut_games(root):
    out = {}
    accts = _accounts_by_recent(root)
    if not accts:
        return out
    try:
        with open(os.path.join(accts[0], "config", "shortcuts.vdf"), "rb") as f:
            d = vdf.binary_loads(f.read(4 * 2**20))
    except (OSError, vdf.VDFError):
        return out
    for v in (d.get("shortcuts") or {}).values():
        if not isinstance(v, dict):
            continue
        exe = str(vdf.get_ci(v, "Exe") or "")
        name = str(vdf.get_ci(v, "AppName") or "")
        if not name or SC.MARK in exe or _int(vdf.get_ci(v, "IsHidden")):
            continue
        aid = vdf.get_ci(v, "appid")
        aid = (_int(aid) & 0xFFFFFFFF) if aid is not None else SC.short_appid(exe, name)
        appid = str(aid)
        out[appid] = {"appid": appid, "name": name, "source": "shortcut", "installed": True, "updating": False,
                      "size_bytes": 0, "last_played": _int(vdf.get_ci(v, "LastPlayTime")), "playtime_min": 0}
    return out


def art_path(root, appid, kind):
    """Path of the best artwork for an AppID, or None. Inputs are validated here."""
    if not valid_appid(appid) or kind not in ART_KINDS:
        return None
    for acct in _accounts_by_recent(root):
        for pat in _GRID[kind]:
            p = os.path.join(acct, "config", "grid", pat.format(a=appid))
            if os.path.isfile(p):
                return p
    cache = os.path.join(root, "appcache", "librarycache")
    for pat in _FLAT[kind]:
        p = os.path.join(cache, pat.format(a=appid))
        if os.path.isfile(p):
            return p
    d = os.path.join(cache, appid)
    if os.path.isdir(d):
        for name in _DIRS[kind]:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
        for sub in sorted(glob.glob(os.path.join(d, "*"))):
            if os.path.isdir(sub):
                for name in _DIRS[kind]:
                    p = os.path.join(sub, name)
                    if os.path.isfile(p):
                        return p
    return None


def scan(root=None):
    """Everything STONE Home lists, most recently played first."""
    root = root or SC.steam_root()
    games = installed_games(root)
    apply_localconfig(root, games)
    for k, v in shortcut_games(root).items():
        games.setdefault(k, v)
    items = sorted(games.values(), key=lambda g: (-g["last_played"], g["name"].lower()))[:MAX_ITEMS]
    for g in items:
        g["art"] = {k: art_path(root, g["appid"], k) is not None for k in ("capsule", "hero")}
        g["launch"] = launch_url(g)
    return {"root_found": os.path.isdir(os.path.join(root, "steamapps")), "games": items}


def launch_url(g):
    if g["source"] == "shortcut":
        return f"steam://rungameid/{SC.game_id(int(g['appid']))}"
    return f"steam://rungameid/{g['appid']}"
