"""One request dispatcher for every transport (WebKit in STONE OS, HTTP in development).

A request is {"method": str, "args": list}. The method must be in the table below; the
arguments are checked by the module that owns them. The result goes back through
done(result) or done(None, code, message), with the same error codes as stoned.
"""

import threading

from gi.repository import GLib

from . import bridge
from . import emulators
from . import library
from . import shortcuts
from . import steamcef
from . import system


class Host:
    def __init__(self, emit, stoned=None, sysapi=None, steam_root=None):
        self.emit = emit                                      # emit(event_name, data)
        self.stoned = stoned or bridge.Stoned(emit)
        self.sys = sysapi or system.System()
        self.steam_root = steam_root
        self.games = {}                                       # appid -> launch URL, from the last scan
        self.owned = set()                                    # not installed, in the account: may be installed
        self.emu_busy = set()                                 # catalog keys being installed
        self.selftest = None                                  # set by app.py --selftest
        self.restart = None                                   # set by app.py: restart STONE Home
        self.steam_ui = steamcef                              # Steam's interface (replaced in tests)

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
            # Games in the account that are not installed come from Steam's interface (the
            # files on disk only list installed ones). Without it, the library is what is installed.
            owned = self.steam_ui.owned_games() if not res.get("error") else None
            res["games"] += library.not_installed(root, owned, {g["appid"] for g in res["games"]})
            res["owned_known"] = owned is not None
            GLib.idle_add(lambda: (self._scanned(res, done), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def _scanned(self, res, done):
        self.games = {g["appid"]: g["launch"] for g in res["games"] if g.get("launch")}
        self.owned = {g["appid"] for g in res["games"] if not g.get("installed") and g.get("source") == "steam"}
        done(res)

    def m_install(self, args, done):
        """Opens Steam's own install dialog for a game of the account that is not installed."""
        if len(args) != 1 or args[0] not in self.owned or not args[0].isdigit():
            return done(None, "InvalidValue", "unknown game")
        appid = int(args[0])
        self._in_steam_ui(lambda: self.steam_ui.install(appid), None, done)

    def m_emulators(self, args, done):
        def work():
            rows = emulators.catalog()
            for r in rows:
                r["busy"] = r["key"] in self.emu_busy
            GLib.idle_add(lambda: (done(rows), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def m_emuInstall(self, args, done):
        if len(args) != 1 or args[0] not in emulators.CATALOG:
            return done(None, "InvalidValue", "unknown emulator")
        key = args[0]
        if key in self.emu_busy:
            return done({"ok": True, "already": True})
        self.emu_busy.add(key)

        def progress(state, text):
            GLib.idle_add(lambda: (self.emit("emulator", {"key": key, "state": state, "text": text}), False)[1])

        def work():
            try:
                emulators.install(key, progress, steam_ui=self.steam_ui)
            finally:
                self.emu_busy.discard(key)
        threading.Thread(target=work, daemon=True).start()
        done({"ok": True})

    def _in_steam_ui(self, call, fallback, done):
        """Ask Steam's interface first (in Game Mode steam:// URLs are received and ignored for
        some targets, board RC3); the steam:// URL is the fallback. Runs off the UI thread."""
        def work():
            st = call()
            ok = st.endswith(": ok")
            err = None
            if not ok and fallback:
                try:
                    fallback()
                    ok = True
                except (OSError, ValueError) as e:
                    err = str(e)
            print(f"[stone] steam ui: {st}{'' if ok or not err else ' / ' + err}", flush=True)
            res = ({"ok": True, "via": "steam-ui" if st.endswith(": ok") else "url"}, None, "") if ok \
                else (None, "Unsupported", st)
            GLib.idle_add(lambda: (done(*res), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def m_launch(self, args, done):
        if len(args) != 1 or args[0] not in self.games:
            return done(None, "InvalidValue", "unknown game")
        url = self.games[args[0]]
        gid = url.rsplit("/", 1)[-1]
        if not gid.isdigit():
            return done(None, "InvalidValue", "unknown game")
        self._in_steam_ui(lambda: self.steam_ui.run_game(int(gid)), lambda: self.sys.open_url(url), done)

    def m_openSteam(self, args, done):
        if len(args) != 1 or args[0] not in system.STEAM_URLS:
            return done(None, "InvalidValue", "unknown target")
        page = {"downloads": "Downloads"}.get(args[0])
        url = system.STEAM_URLS[args[0]]
        call = (lambda: self.steam_ui.open_page(page)) if page else (lambda: "cef: not used")
        self._in_steam_ui(call, lambda: self.sys.open_url(url), done)

    def m_openSteamSettings(self, args, done):
        if len(args) != 1 or args[0] not in steamcef.SETTINGS_SECTIONS:
            return done(None, "InvalidValue", "unknown settings section")
        section = args[0]
        self._in_steam_ui(lambda: self.steam_ui.open_settings(section), None, done)

    def m_restart(self, args, done):
        """Restart STONE Home (a new drawing mode applies at start)."""
        if self.restart is None:
            return done(None, "Unsupported", "restart not available")
        done({"ok": True})
        GLib.timeout_add(300, lambda: (self.restart(), False)[1])

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
