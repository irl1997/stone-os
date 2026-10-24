"""One request dispatcher for every transport (WebKit in STONE OS, HTTP in development).

A request is {"method": str, "args": list}. The method must be in the table below; the
arguments are checked by the module that owns them. The result goes back through
done(result) or done(None, code, message), with the same error codes as stoned.
"""

import threading

from gi.repository import GLib

from . import bridge
from . import library
from . import shortcuts
from . import system


class Host:
    def __init__(self, emit, stoned=None, sysapi=None, steam_root=None):
        self.emit = emit                                      # emit(event_name, data)
        self.stoned = stoned or bridge.Stoned(emit)
        self.sys = sysapi or system.System()
        self.steam_root = steam_root
        self.games = {}                                       # appid -> launch URL, from the last scan
        self.selftest = None                                  # set by app.py --selftest

    def root(self):
        return self.steam_root or shortcuts.steam_root()

    def handle(self, req, done):
        if not isinstance(req, dict) or not isinstance(req.get("method"), str):
            return done(None, "InvalidValue", "bad request")
        method, args = req["method"], req.get("args", [])
        if not isinstance(args, list) or len(args) > 4:
            return done(None, "InvalidValue", "bad arguments")
        try:
            if method.startswith("perf."):
                return self.stoned.call(method[5:], args, done)
            fn = getattr(self, "m_" + method.replace(".", "_"), None) if method.replace(".", "").isalnum() else None
            if fn is None:
                return done(None, "InvalidValue", "unknown method")
            return fn(args, done)
        except Exception as e:        # a bad request never takes STONE Home down
            return done(None, "HardwareError", f"internal error: {e!r}")

    # ---- library and launching --------------------------------------------------------
    def m_library(self, args, done):
        root = self.root()

        def work():
            try:
                res = library.scan(root)
            except Exception as e:  # damaged Steam files: show an empty library, say why
                res = {"root_found": False, "games": [], "error": repr(e)}
            GLib.idle_add(lambda: (self._scanned(res, done), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _scanned(self, res, done):
        self.games = {g["appid"]: g["launch"] for g in res["games"]}
        done(res)

    def m_launch(self, args, done):
        if len(args) != 1 or args[0] not in self.games:
            return done(None, "InvalidValue", "unknown game")
        try:
            self.sys.open_url(self.games[args[0]])
        except (OSError, ValueError) as e:
            return done(None, "Unsupported", str(e))
        done({"ok": True})

    def m_openSteam(self, args, done):
        if len(args) != 1 or args[0] not in system.STEAM_URLS:
            return done(None, "InvalidValue", "unknown target")
        try:
            self.sys.open_url(system.STEAM_URLS[args[0]])
        except OSError as e:
            return done(None, "Unsupported", str(e))
        done({"ok": True})

    def art_path(self, appid, kind):
        return library.art_path(self.root(), appid, kind)

    # ---- system --------------------------------------------------------------------------
    def m_power(self, args, done):
        if len(args) != 1:
            return done(None, "InvalidValue", "action")
        self.sys.power(args[0], done)

    def m_systemInfo(self, args, done):
        self.sys.info(done)

    def m_prefs_get(self, args, done):
        done(system.load_prefs())

    def m_prefs_set(self, args, done):
        if len(args) != 1 or not isinstance(args[0], dict):
            return done(None, "InvalidValue", "prefs")
        try:
            done(system.save_prefs(args[0]))
        except OSError as e:
            done(None, "HardwareError", str(e))

    def m_selftest(self, args, done):
        if self.selftest is None:
            return done(None, "InvalidValue", "not in self-test mode")
        done({"ok": True})
        self.selftest(args[0] if args else None)
