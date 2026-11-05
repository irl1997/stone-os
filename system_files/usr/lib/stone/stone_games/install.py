"""Components: the upscaler files STONE gets from their authors, and OptiScaler's install in a game.

Only on the person's request (a button or `stone-game components|install`), never at boot, never
in the background, never for a game that has anti-cheat or is online-only.

  latest(kind)             the newest official release of the author's GitHub repository
  fetch(kind)              download it, check the size and the SHA-256 GitHub states, unpack it safely
                           into  <data>/stone/components/<kind>/<tag>/   (nothing is installed in a game)
  install_plan(appid, ..)  what installing OptiScaler in this game would do (folder, files)
  install_optiscaler(..)   copy the files into the game's executable folder as winmm.dll (OptiScaler's
                           documented manual install). Nothing existing is overwritten. The list of
                           files is saved so uninstall removes exactly them.
  uninstall(appid)         remove the files STONE copied
  helixsr_command()        HelixSR: the author's own installer is interactive and installs into every
                           game it finds, so STONE downloads it and gives the command; the person runs it.

Sources (author's official repositories; STONE bundles nothing):
  helixsr     github.com/lonewolf0622/HelixSR       asset HelixSR-<version>.zip
  optiscaler  github.com/optiscaler/OptiScaler      asset OptiScaler*.7z or .zip
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

from . import graphics

SOURCES = {
    "helixsr": {"repo": "lonewolf0622/HelixSR", "asset": r"^HelixSR-[0-9][0-9A-Za-z._-]*\.zip$", "label": "HelixSR"},
    "optiscaler": {"repo": "optiscaler/OptiScaler", "asset": r"^OptiScaler.*\.(7z|zip)$", "label": "OptiScaler (FSR 3.1)"},
}
HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_BYTES = 400 * 2**20
SKIP_EXE = re.compile(r"(unins|crash|launcher|redist|setup|install|dxsetup|vc_|dotnet|ue4prereq|epicweb|eac|battleye|updater|helper|report)", re.I)
PROXY_NAME = "winmm.dll"


class InstallError(ValueError):
    pass


def components_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "stone", "components")


def installs_dir():
    return os.path.join(components_dir(), "installs")


# ---- network (replaced in tests) -------------------------------------------------------------
def _check_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in HOSTS:
        raise InstallError(f"address not allowed: {u.hostname}")


def http_json(url):
    _check_url(url)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "stone-os"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http_download(url, dest):
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "stone-os"})
    h = hashlib.sha256()
    n = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            n += len(b)
            if n > MAX_BYTES:
                raise InstallError("download too large")
            h.update(b)
            f.write(b)
    return h.hexdigest(), n


# ---- releases --------------------------------------------------------------------------------
def latest(kind, get_json=None):
    src = SOURCES.get(kind)
    if not src:
        raise InstallError("unknown component")
    rel = (get_json or http_json)(f"https://api.github.com/repos/{src['repo']}/releases/latest")
    if not isinstance(rel, dict) or not isinstance(rel.get("tag_name"), str):
        raise InstallError("unexpected answer from GitHub")
    tag = re.sub(r"[^0-9A-Za-z._-]", "", rel["tag_name"])[:40]
    if not tag:
        raise InstallError("bad release name")
    rx = re.compile(src["asset"])
    assets = [a for a in rel.get("assets") or [] if isinstance(a, dict) and rx.match(str(a.get("name", "")))]
    if not assets:
        raise InstallError(f"{src['label']}: the latest release has no file I recognise ({tag})")
    # a .zip first (no extra tool needed), then the smallest name
    assets.sort(key=lambda a: (not str(a["name"]).endswith(".zip"), len(str(a["name"]))))
    a = assets[0]
    digest = a.get("digest") if isinstance(a.get("digest"), str) and a["digest"].startswith("sha256:") else None
    return {"kind": kind, "tag": tag, "name": a["name"], "url": a.get("browser_download_url"),
            "size": a.get("size"), "sha256": digest[7:] if digest else None, "prerelease": bool(rel.get("prerelease"))}


def _safe_name(n):
    return re.sub(r"[^0-9A-Za-z._-]", "_", n)[:100]


def _extract_zip(path, dest):
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            target = os.path.realpath(os.path.join(dest, i.filename))
            if not (target == os.path.realpath(dest) or target.startswith(os.path.realpath(dest) + os.sep)):
                raise InstallError("the archive tries to write outside its folder")
            mode = (i.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                continue                                        # no symbolic links from an archive
        z.extractall(dest)
    for d, _dirs, files in os.walk(dest):
        for f in files:
            if f.endswith(".sh"):
                os.chmod(os.path.join(d, f), 0o755)


def _extract_7z(path, dest):
    for tool, args in (("bsdtar", ["-xf", path, "-C", dest]), ("7z", ["x", "-y", "-o" + dest, path]),
                       ("7za", ["x", "-y", "-o" + dest, path]), ("7zr", ["x", "-y", "-o" + dest, path])):
        exe = shutil.which(tool)
        if exe:
            r = subprocess.run([exe, *args], capture_output=True, timeout=300)
            if r.returncode != 0:
                raise InstallError(f"{tool} could not unpack the file")
            break
    else:
        raise InstallError("this release is a .7z and no tool to open it is installed (bsdtar or 7z)")
    for d, dirs, files in os.walk(dest):
        for n in dirs + files:
            if os.path.islink(os.path.join(d, n)):
                raise InstallError("the archive contains a symbolic link")


def fetch(kind, get_json=None, download=None, log=lambda m: None):
    """Download + verify + unpack the latest official release. Returns the component record."""
    info = latest(kind, get_json)
    base = os.path.join(components_dir(), kind, info["tag"])
    rec_path = os.path.join(base, "component.json")
    if os.path.isfile(rec_path):
        with open(rec_path) as f:
            return dict(json.load(f), already=True)
    if not info["url"]:
        raise InstallError("the release has no download address")
    tmp = tempfile.mkdtemp(prefix="stone-component-")
    try:
        arc = os.path.join(tmp, _safe_name(info["name"]))
        log(f"[stone] components: downloading {info['name']}")
        sha, n = (download or http_download)(info["url"], arc)
        if info["size"] and n != info["size"]:
            raise InstallError(f"size differs from GitHub's ({n} bytes, expected {info['size']})")
        if info["sha256"] and sha != info["sha256"]:
            raise InstallError("the file's SHA-256 does not match the one GitHub states: not installed")
        out = os.path.join(tmp, "x")
        os.makedirs(out)
        (_extract_zip if arc.endswith(".zip") else _extract_7z)(arc, out)
        os.makedirs(os.path.dirname(base), exist_ok=True)
        if os.path.isdir(base):
            shutil.rmtree(base)
        shutil.move(out, base)
        rec = {"kind": kind, "tag": info["tag"], "asset": info["name"], "sha256": sha, "size": n,
               "verified_by_github": bool(info["sha256"]), "source": info["url"], "time": int(time.time()),
               "prerelease": info["prerelease"]}
        with open(rec_path, "w") as f:
            json.dump(rec, f)
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def have(kind):
    """The newest component of this kind already on the console, or None."""
    d = os.path.join(components_dir(), kind)
    best = None
    try:
        for t in os.listdir(d):
            p = os.path.join(d, t, "component.json")
            if os.path.isfile(p):
                with open(p) as f:
                    r = json.load(f)
                if best is None or r.get("time", 0) > best.get("time", 0):
                    best = dict(r, path=os.path.join(d, t))
    except (OSError, ValueError):
        return None
    return best


def status():
    out = []
    for k, s in SOURCES.items():
        h = have(k)
        out.append({"kind": k, "label": s["label"], "have": {x: h[x] for x in ("tag", "sha256", "time", "verified_by_github")} if h else None})
    return out


# ---- OptiScaler in a game ----------------------------------------------------------------------
def find_exe_dir(folder):
    """The folder of the game's executable: where the largest game .exe is."""
    best = None
    top = os.path.realpath(folder)
    base = top.count(os.sep)
    for d, dirs, files in os.walk(top):
        if d.count(os.sep) - base >= 5:
            dirs[:] = []
        for f in files:
            if f.lower().endswith(".exe") and not SKIP_EXE.search(f):
                try:
                    sz = os.path.getsize(os.path.join(d, f))
                except OSError:
                    continue
                if best is None or sz > best[0]:
                    best = (sz, d, f)
    return (best[1], best[2]) if best else (None, None)


def _component_root(path):
    for d, dirs, files in os.walk(path):
        if d.count(os.sep) - path.count(os.sep) > 3:
            dirs[:] = []
        if any(f.lower() == "optiscaler.dll" for f in files):
            return d
    return None


def _install_path(appid):
    return os.path.join(installs_dir(), f"{int(appid)}.json")


def installed_by_stone(appid):
    try:
        with open(_install_path(appid)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def install_plan(appid, facts, game):
    """What installing OptiScaler would do, or why not. facts: detect.facts(); game: profile["game"]."""
    if not facts.get("installed") or not facts.get("install_dir"):
        return {"ok": False, "error": "the game is not installed"}
    why = graphics.safe_reason(game, facts)
    if why:
        return {"ok": False, "error": f"not for this game ({why})"}
    if facts.get("injected"):
        return {"ok": False, "error": "an upscaler is already installed in this game"}
    comp = have("optiscaler")
    if not comp:
        return {"ok": False, "needs_download": True, "error": "OptiScaler is not downloaded yet"}
    src = _component_root(comp["path"])
    if not src:
        return {"ok": False, "error": "the downloaded OptiScaler has an unexpected layout (no OptiScaler.dll)"}
    exe_dir, exe = find_exe_dir(facts["install_dir"])
    if not exe_dir:
        return {"ok": False, "error": "could not find the game's executable"}
    pairs = []                                            # (file in the component, name in the game folder)
    for d, _dirs, fs in os.walk(src):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), src)
            pairs.append((os.path.join(d, f), PROXY_NAME if rel.lower() == "optiscaler.dll" else rel))
    files = [p[1] for p in pairs]
    clash = [f for f in files if os.path.lexists(os.path.join(exe_dir, f))]
    if clash:
        return {"ok": False, "error": "files with the same name already exist in the game folder: " + ", ".join(clash[:3])}
    rel_dir = os.path.relpath(exe_dir, facts["install_dir"])
    return {"ok": True, "component": {"tag": comp["tag"]}, "exe": exe, "folder": rel_dir if rel_dir != "." else "(carpeta del juego)",
            "files": len(files), "_pairs": pairs, "_exe_dir": exe_dir}


def install_optiscaler(appid, facts, game):
    plan = install_plan(appid, facts, game)
    if not plan["ok"]:
        return {k: v for k, v in plan.items() if not k.startswith("_")}
    exe_dir, pairs = plan["_exe_dir"], plan["_pairs"]
    done = []
    try:
        for origin, f in pairs:
            dest = os.path.join(exe_dir, f)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(origin, dest)
            done.append(f)
    except Exception:                                  # noqa: BLE001  leave nothing half-installed
        for f in done:
            try:
                os.remove(os.path.join(exe_dir, f))
            except OSError:
                pass
        raise
    os.makedirs(installs_dir(), exist_ok=True)
    rec = {"appid": str(appid), "kind": "optiscaler", "tag": plan["component"]["tag"], "exe_dir": exe_dir, "files": done,
           "time": int(time.time())}
    with open(_install_path(appid), "w") as f:
        json.dump(rec, f)
    return {"ok": True, "installed": plan["component"]["tag"], "folder": plan["folder"], "files": len(done)}


def uninstall(appid):
    rec = installed_by_stone(appid)
    if not rec:
        return {"ok": False, "error": "STONE did not install anything in this game"}
    removed = 0
    exe_dir = rec["exe_dir"]
    for f in rec.get("files", []):
        p = os.path.realpath(os.path.join(exe_dir, f))
        if not p.startswith(os.path.realpath(exe_dir) + os.sep):
            continue
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    for f in sorted(rec.get("files", []), key=len, reverse=True):       # empty folders STONE created
        d = os.path.dirname(os.path.join(exe_dir, f))
        while os.path.realpath(d) != os.path.realpath(exe_dir):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)
    os.remove(_install_path(appid))
    return {"ok": True, "removed": removed}


def helixsr_command():
    """The author's installer, downloaded: the person runs it (it asks which games)."""
    c = have("helixsr")
    if not c:
        return {"ok": False, "needs_download": True, "error": "HelixSR is not downloaded yet"}
    sh = None
    for d, dirs, files in os.walk(c["path"]):
        if d.count(os.sep) - c["path"].count(os.sep) > 3:
            dirs[:] = []
        if "helixsr-setup.sh" in files:
            sh = os.path.join(d, "helixsr-setup.sh")
            break
    if not sh:
        return {"ok": False, "error": "the downloaded HelixSR has no helixsr-setup.sh"}
    return {"ok": True, "tag": c["tag"], "dir": os.path.dirname(sh),
            "command": f"cd '{os.path.dirname(sh)}' && ./helixsr-setup.sh",
            "warning": "No uses --yes: instalaría HelixSR en TODOS los juegos, también en los que tienen anti-cheat. "
                       "El instalador te pregunta; elige solo juegos sin anti-cheat y sin modo online."}
