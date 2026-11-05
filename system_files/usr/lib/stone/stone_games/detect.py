"""Facts about an installed game, read from disk (no game is started, nothing is changed).

  install_dir    <library>/steamapps/common/<installdir> from the game's appmanifest
  build          Steam's buildid (game version) from the appmanifest
  anti_cheat     EasyAntiCheat / BattlEye / other kernel-level anti-cheat files in the game folder
  injected       upscaler injections already installed by the person in the game folder:
                   helixsr     HelixSR (HelixSR/helixsr.dll next to OptiScaler's proxy DLL)
                   optiscaler  OptiScaler (OptiScaler.ini + proxy DLL), without HelixSR
                 with the proxy DLL name (dxgi, winmm, version, ...) and a fingerprint of the
                 files (size + mtime) so an update of HelixSR/OptiScaler invalidates measurements
  proton         the compatibility tool Steam uses for this game (config.vdf), or None (default)

STONE bundles neither. On the person's request only (install.py) it downloads them from their authors
and installs OptiScaler in a game; this module just reads what is there, whoever installed it.
"""

import glob
import hashlib
import os

from stone_home import library, vdf

PROXIES = ("dxgi.dll", "winmm.dll", "version.dll", "d3d12.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll")
ANTI_CHEAT = (("EasyAntiCheat", "EasyAntiCheat"), ("EasyAntiCheat_EOS.sys", "EasyAntiCheat"),
              ("BattlEye", "BattlEye"), ("BEService_x64.exe", "BattlEye"), ("vgk.sys", "Vanguard"),
              ("mhyprot2.sys", "mhyprot"), ("xigncode", "XIGNCODE"), ("nProtect", "nProtect"),
              ("EAAntiCheat.GameServiceLauncher.exe", "EA Anti-Cheat"))
MAX_DEPTH = 4


def manifest(root, appid):
    for lib in library.library_paths(root):
        acf = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        try:
            st = vdf.get_ci(vdf.load_file(acf, limit=2**20), "AppState")
        except (OSError, vdf.VDFError):
            continue
        if isinstance(st, dict):
            d = vdf.get_ci(st, "installdir")
            return {"library": lib, "installdir": d if isinstance(d, str) else None,
                    "build": str(vdf.get_ci(st, "buildid") or "") or None, "name": vdf.get_ci(st, "name")}
    return None


def _walk(top, depth=MAX_DEPTH):
    top = os.path.realpath(top)
    base = top.count(os.sep)
    for d, dirs, files in os.walk(top):
        if d.count(os.sep) - base >= depth:
            dirs[:] = []
        yield d, dirs, files


def _fp(paths):
    h = hashlib.sha1()
    for p in sorted(paths):
        try:
            s = os.stat(p)
            h.update(f"{os.path.basename(p)}:{s.st_size}:{int(s.st_mtime)}".encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


def scan_folder(folder):
    out = {"anti_cheat": [], "injected": None}
    if not folder or not os.path.isdir(folder):
        return out
    found_ac = set()
    for d, dirs, files in _walk(folder):
        names = set(dirs) | set(files)
        for marker, name in ANTI_CHEAT:
            if any(n.lower() == marker.lower() or (marker.lower() in n.lower() and len(marker) > 8) for n in names):
                found_ac.add(name)
        lower = {f.lower(): f for f in files}
        if "optiscaler.ini" in lower and out["injected"] is None:
            proxy = next((p for p in PROXIES if p in lower), None)
            helix = os.path.isfile(os.path.join(d, "HelixSR", "helixsr.dll")) or os.path.isfile(os.path.join(d, "helixsr", "helixsr.dll"))
            files_fp = [os.path.join(d, lower["optiscaler.ini"])] + ([os.path.join(d, lower[proxy])] if proxy else []) + \
                glob.glob(os.path.join(d, "[Hh]elix[Ss][Rr]", "*.dll"))
            out["injected"] = {"kind": "helixsr" if helix else "optiscaler", "dir": d, "proxy": proxy[:-4] if proxy else None,
                               "fingerprint": _fp(files_fp)}
    out["anti_cheat"] = sorted(found_ac)
    return out


def proton_tool(root, appid):
    try:
        d = vdf.load_file(os.path.join(root, "config", "config.vdf"), limit=8 * 2**20)
    except (OSError, vdf.VDFError):
        return None
    m = vdf.get_ci(d, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping") or {}
    e = m.get(str(appid)) if isinstance(m, dict) else None
    if isinstance(e, dict):
        name = vdf.get_ci(e, "name")
        return name if isinstance(name, str) and name else None
    return None


def facts(root, appid):
    """Everything above for one Steam game; None fields are unknown."""
    m = manifest(root, appid)
    folder = os.path.join(m["library"], "steamapps", "common", m["installdir"]) if m and m.get("installdir") else None
    scan = scan_folder(folder)
    return {"appid": str(appid), "installed": bool(folder and os.path.isdir(folder)), "install_dir": folder,
            "build": m.get("build") if m else None, "anti_cheat": scan["anti_cheat"], "injected": scan["injected"],
            "proton": proton_tool(root, appid)}
