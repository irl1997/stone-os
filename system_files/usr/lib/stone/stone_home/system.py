"""What STONE Home asks of the rest of the system, besides performance.

  power        logind PowerOff / Reboot (the active local session is allowed by the
               default polkit rules; no root, no shell)
  steam URLs   a fixed set of steam:// URLs handed to the running Steam client
  system info  /usr/lib/stone-release, the boot time and the image deployments
  prefs        STONE Home's own settings in ~/.config/stone/home.json

Nothing here runs a shell or a command built from UI input. The only commands are
`steam <url>` with a URL from STEAM_URLS or library.launch_url(), and
`rpm-ostree status --json`.
"""

import json
import os
import subprocess
import tempfile
import threading

from gi.repository import Gio, GLib

STEAM_BIN = "/usr/bin/steam"
# Steam browser protocol (https://developer.valvesoftware.com/wiki/Steam_browser_protocol).
# [VERIFICAR EN PLACA] how each one shows inside Game Mode.
STEAM_URLS = {
    "store": "steam://url/StoreFrontPage",
    "downloads": "steam://open/downloads",
    "screenshots": "steam://open/screenshots",
}
RELEASE = os.environ.get("STONE_RELEASE_FILE", "/usr/lib/stone-release")


def prefs_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "stone", "home.json")


# key -> (validator, default)
def _bool(v):
    return v if isinstance(v, bool) else None


def _intr(lo, hi):
    return lambda v: v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _vol(v):
    return round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 else None


def _favs(v):
    if not isinstance(v, list):
        return None
    out = [a for a in v if isinstance(a, str) and a.isdigit() and 0 < len(a) <= 10]
    return out[:500]


PREFS = {
    "sound": (_bool, True), "vol": (_vol, 0.7), "hc": (_bool, False), "rm": (_bool, False),
    "ts": (_intr(0, 2), 0), "safe": (_intr(0, 8), 5), "idle": (_intr(0, 3), 2), "tech": (_bool, False),
    "adv_ack": (_bool, False), "favorites": (_favs, []),
}


def clean_prefs(d):
    out = {}
    for k, (check, default) in PREFS.items():
        v = check(d.get(k)) if isinstance(d, dict) and k in d else None
        out[k] = default if v is None else v
    return out


def load_prefs():
    try:
        with open(prefs_path()) as f:
            return clean_prefs(json.load(f))
    except (OSError, ValueError):
        return clean_prefs({})


def save_prefs(d):
    p = clean_prefs(d)
    path = prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".home-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(p, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return p


def read_release(path=None):
    out = {}
    try:
        with open(path or RELEASE) as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k.startswith("STONE_"):
                    out[k] = v.strip().strip('"')
    except OSError:
        pass
    return out


def boot_time(proc="/proc"):
    try:
        with open(os.path.join(proc, "stat")) as f:
            for line in f:
                if line.startswith("btime "):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def deployments(run=subprocess.run):
    """Booted, staged and rollback images from rpm-ostree (read-only, unprivileged)."""
    try:
        out = run(["rpm-ostree", "status", "--json"], capture_output=True, text=True, timeout=20).stdout
        d = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    res = {"booted": None, "staged": None, "rollback": None}
    for dep in d.get("deployments", []):
        info = {"image": dep.get("container-image-reference") or dep.get("origin"),
                "version": dep.get("version"), "timestamp": dep.get("timestamp")}
        if dep.get("booted"):
            res["booted"] = info
        elif dep.get("staged"):
            res["staged"] = info
        elif res["rollback"] is None and res["booted"] is not None:
            res["rollback"] = info
    return res


class System:
    def __init__(self, bus=None, spawn=None):
        self._bus = bus
        self.spawn = spawn or self._spawn_steam

    def bus(self):
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self._bus

    @staticmethod
    def _spawn_steam(url):
        subprocess.Popen([STEAM_BIN, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

    def open_url(self, url):
        if not (url.startswith("steam://rungameid/") and url[18:].isdigit()) and url not in STEAM_URLS.values():
            raise ValueError("url not allowed")
        self.spawn(url)

    def power(self, action, done):
        method = {"off": "PowerOff", "reboot": "Reboot"}.get(action)
        if method is None:
            return done(None, "InvalidValue", "action")

        def finished(conn, res):
            try:
                conn.call_finish(res)
                done({"ok": True})
            except GLib.Error as e:
                done(None, "PermissionDenied" if "Access" in (Gio.DBusError.get_remote_error(e) or "") else "Unsupported",
                     e.message)

        self.bus().call("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                        method, GLib.Variant("(b)", (False,)), None, Gio.DBusCallFlags.NONE, 10000, None, finished)

    def info(self, done):
        def work():
            res = {"release": read_release(), "boot_time": boot_time(), "deployments": deployments()}
            GLib.idle_add(lambda: (done(res), False)[1])
        threading.Thread(target=work, daemon=True).start()
