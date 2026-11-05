"""One request dispatcher for every transport (WebKit in STONE OS, HTTP in development).

A request is {"method": str, "args": list}. The method must be in the table below; the
arguments are checked by the module that owns them. The result goes back through
done(result) or done(None, code, message), with the same error codes as stoned.
"""

import os
import threading

from gi.repository import GLib

import stone_display
from stone_games import optimizer as games_opt
from stone_games import schema as games_schema
from stone_games.perf import PerfAsync

from . import bridge
from . import emulators
from . import library
from . import shortcuts
from . import steamcef
from . import steamsettings
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
        cef = _SteamUIProxy(self)
        # STONE DISPLAY: display model and the game resolution environment (stone_display/).
        self.display = stone_display.Display(game_policy=self.game_policy, cef=cef,
                                             goal=lambda: system.load_prefs().get("display_goal", "fluid"),
                                             on_change=lambda why: GLib.idle_add(self._display_changed, why))
        self.game_env = stone_display.GameEnvironment(cef=cef)
        # STONE OPTIMIZED: game profiles, graphics paths, measured optimization (stone_games/).
        self.optimized = games_opt.Engine(root=self.root, cef=cef, display=lambda: self.display.status(fresh=True),
                                          perf=PerfAsync(self.stoned), log=lambda m: print(m, flush=True))
        self._opt_busy = False
        GLib.timeout_add_seconds(3, self._opt_monitor)
        GLib.timeout_add_seconds(8, lambda: (self._opt_work(self.optimized.recover, "recover"), False)[1])

    def game_policy(self):
        """"native": games see the display's own resolution; "steam": whatever Steam defaults to."""
        return "steam" if system.load_prefs().get("game_res") == 1 else "native"

    def _display_changed(self, why):
        print(f"[stone] display: DISPLAY_CHANGED ({why})", flush=True)
        self.emit("display", {"changed": True})
        return False

    # ---- STONE OPTIMIZED: monitor and recovery (off the UI thread) ---------------------------
    def _opt_work(self, fn, what):
        if self._opt_busy:
            return
        self._opt_busy = True

        def work():
            try:
                r = fn()
                if isinstance(r, dict) and r:
                    print(f"[stone] optimized: {what}: {r}", flush=True)
                    if what == "monitor" and r.get("appid"):
                        GLib.idle_add(lambda: (self.emit("game", {"ended": r}), False)[1])
            except Exception as e:  # noqa: BLE001  (never take STONE Home down over this)
                print(f"[stone] optimized: {what} failed: {e!r}", flush=True)
            finally:
                self._opt_busy = False
        threading.Thread(target=work, daemon=True).start()

    def _opt_monitor(self):
        """MONITOR: only while a game launched by STONE OPTIMIZED is prepared or running."""
        if os.path.exists(self.optimized.session_path):
            self._opt_work(self.optimized.tick, "monitor")
        return True

    def optimized_on(self):
        return system.load_prefs().get("optimized", 1) == 1

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
        except games_schema.ProfileError as e:
            return done(None, "InvalidValue", str(e))
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
        # The game environment for every game, known or not: installed games, shortcuts and STONE itself.
        if self.game_policy() == "native":
            ids = [g["appid"] for g in res["games"] if g.get("installed")]
            root = self.root()
            threading.Thread(target=lambda: self._apply_env(ids + list(shortcuts.shortcut_appids(root))),
                             daemon=True).start()

    def _env_state(self):
        snap = self.display.status(fresh=True)
        # The size to give games: the display's native one, or the best the link carries when it cannot do native.
        return ({"game_default": snap.get("steam_game_default")} if snap.get("steam_available") else None,
                snap.get("game_environment") or (snap.get("primary") or {}).get("native_resolution"))

    def _apply_env(self, ids):
        try:
            d = self.game_env.ensure_default(self._env_state)        # every game, wherever launched
            if d.get("status") not in ("ok", "changed", "unsupported", "unknown"):
                print(f"[stone] display: default game size: {d}", flush=True)
            r = self.game_env.apply(ids)                              # per game, where still "Default"
            if r.get("error"):
                print(f"[stone] display: game environment not applied ({r['error']})", flush=True)
        except Exception as e:  # noqa: BLE001  (never take STONE Home down over this)
            print(f"[stone] display: game environment failed: {e!r}", flush=True)

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
        appid = args[0]

        def launch():
            if self.game_policy() == "native":
                self._apply_env([appid])          # a game installed since the last scan, too
            if gid == appid and self.optimized_on():
                # GAME -> STONE RECOMMENDED -> PLAY. Any failure here launches the game as it is.
                try:
                    r = self.optimized.prepare(appid)
                    print(f"[stone] optimized: {appid}: {r.get('why') or r.get('error')} "
                          f"graphics={r.get('graphics_applied')} performance={r.get('performance')}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[stone] optimized: {appid} not prepared ({e!r}); launching unchanged", flush=True)
            return self.steam_ui.run_game(int(gid))
        self._in_steam_ui(launch, lambda: self.sys.open_url(url), done)

    # ---- STONE OPTIMIZED API ---------------------------------------------------------------
    def _opt_call(self, done, fn):
        def work():
            try:
                res = (fn(), None, "")
            except games_schema.ProfileError as e:
                res = (None, "InvalidValue", str(e))
            except Exception as e:  # noqa: BLE001
                res = (None, "HardwareError", f"optimized: {e!r}")
            GLib.idle_add(lambda: (done(*res), False)[1])
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _game_args(args, n_min=1, n_max=2):
        if not n_min <= len(args) <= n_max or not isinstance(args[0], str) or not args[0].isdigit() or len(args[0]) > 10:
            raise games_schema.ProfileError("appid")
        mode = args[1] if len(args) > 1 else None
        if mode is not None and mode not in games_schema.MODES:
            raise games_schema.ProfileError("mode")
        return args[0], mode

    def m_game_profile(self, args, done):
        """getGameProfile(appid)"""
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: frontend_profile(self.optimized.profile(appid)))

    def m_game_recommended(self, args, done):
        """getRecommendedExperience(appid, mode?)"""
        appid, mode = self._game_args(args)
        self._opt_call(done, lambda: frontend_recommended(self.optimized.recommended(appid, mode or self.optimized.chosen_mode(appid))))

    def m_game_modes(self, args, done):
        """getAvailableModes(appid)"""
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: {"chosen": self.optimized.chosen_mode(appid), "modes": self.optimized.modes(appid)})

    def m_game_apply(self, args, done):
        """applyGameProfile(appid, mode, config?) - the mode used from now on (custom: with its config)."""
        if len(args) not in (2, 3):
            return done(None, "InvalidValue", "arguments")
        appid, mode = self._game_args(args[:2], 2, 2)
        cfg = args[2] if len(args) == 3 else None
        if mode == "custom" and not isinstance(cfg, dict):
            return done(None, "InvalidValue", "custom needs a configuration")
        self._opt_call(done, lambda: self.optimized.apply(appid, mode, cfg))

    def m_game_launchOptimized(self, args, done):
        """launchOptimized(appid, mode?)"""
        appid, mode = self._game_args(args)
        if appid not in self.games:
            return done(None, "InvalidValue", "unknown game")
        if mode:
            self.optimized.apply(appid, mode)
        return self.m_launch([appid], done)

    def m_game_image(self, args, done):
        """imageOptions(appid): the enhancement choices of one game and which one is active."""
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: self.optimized.image_options(appid))

    def m_game_setImage(self, args, done):
        """setImage(appid, key, preset?): the person's own choice; unavailable ones are refused."""
        if len(args) not in (2, 3) or not isinstance(args[1], str) or (len(args) == 3 and args[2] is not None and not isinstance(args[2], str)):
            return done(None, "InvalidValue", "arguments")
        appid, _ = self._game_args(args[:1], 1, 1)
        preset = args[2] if len(args) == 3 else None
        self._opt_call(done, lambda: self.optimized.set_image(appid, args[1], preset))

    def m_components(self, args, done):
        """components(kind?): without kind, what is downloaded; with kind, download the newest official release."""
        if not args:
            return self._opt_call(done, self.optimized.components)
        if len(args) != 1 or args[0] not in ("helixsr", "optiscaler"):
            return done(None, "InvalidValue", "component")
        self._opt_call(done, lambda: self.optimized.fetch_component(args[0]))

    def m_game_installPlan(self, args, done):
        """installPlan(appid, kind): what installing would do (nothing is changed)."""
        if len(args) != 2 or args[1] not in ("helixsr", "optiscaler"):
            return done(None, "InvalidValue", "arguments")
        appid, _ = self._game_args(args[:1], 1, 1)
        self._opt_call(done, lambda: self.optimized.install_plan(appid, args[1]))

    def m_game_install(self, args, done):
        """install(appid): OptiScaler into the game (only after installPlan said ok)."""
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: self.optimized.install_image(appid, "optiscaler"))

    def m_game_uninstallImage(self, args, done):
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: self.optimized.uninstall_image(appid))

    # ---- Steam's settings, inside STONE (stone_home/steamsettings.py) -----------------------
    def _steamset(self):
        if getattr(self, "_ss", None) is None:
            host = self

            class Cef:                                    # always the host's current Steam interface (a fake in tests)
                def ui_value(self, e):
                    return host.steam_ui.ui_value(e)

                def probe(self):
                    return host.steam_ui.probe()
            self._ss = steamsettings.SteamSettings(Cef())
        return self._ss

    def m_steam_settings(self, args, done):
        """steam.settings(refresh?): every setting this Steam offers that STONE may show, with its value."""
        refresh = bool(args and args[0] is True)
        self._opt_call(done, lambda: self._steamset().read(refresh))

    def m_steam_setSetting(self, args, done):
        """steam.setSetting(id, true|false): change one true/false setting that is in the catalog."""
        if len(args) != 2 or not isinstance(args[0], str) or not isinstance(args[1], bool) or len(args[0]) > 120:
            return done(None, "InvalidValue", "arguments")

        def work():
            try:
                return self._steamset().set_bool(args[0], args[1])
            except ValueError as e:
                raise games_schema.ProfileError(str(e)) from e
        self._opt_call(done, work)

    def m_game_optimizeStatus(self, args, done):
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: self.optimized.optimize_status(appid))

    def m_game_optimizeStart(self, args, done):
        """Launch the next measurement of the matrix (the person plays or runs the game's benchmark, then quits)."""
        appid, _ = self._game_args(args, 1, 1)
        if appid not in self.games:
            return done(None, "InvalidValue", "unknown game")
        if self.optimized.status().get("active"):
            return done(None, "InvalidValue", "a game is already being optimized")

        def launch():
            if self.game_policy() == "native":
                self._apply_env([appid])
            r = self.optimized.launch(appid, measure={"config": "next", "delay": 90, "duration": 120})
            return {k: r.get(k) for k in ("ok", "error", "settings", "config")} if not r.get("ok") else \
                {"ok": True, "settings": r.get("settings"), "summary": games_opt.summary(r["config"])}
        self._opt_call(done, launch)

    def m_game_status(self, args, done):
        """getOptimizationStatus()"""
        self._opt_call(done, self.optimized.status)

    def m_game_restoreRecommended(self, args, done):
        """restoreRecommended(appid)"""
        appid, _ = self._game_args(args, 1, 1)
        self._opt_call(done, lambda: self.optimized.restore_recommended(appid))

    # ---- display -------------------------------------------------------------------------
    def m_display_status(self, args, done):
        """STONE DISPLAY for the interface (no Linux access from the page). args: [fresh?]"""
        fresh = bool(args and args[0] is True)

        def work():
            try:
                res = (frontend_display(self.display.status(fresh=fresh)), None, "")
            except Exception as e:  # noqa: BLE001
                res = (None, "HardwareError", f"display: {e!r}")
            GLib.idle_add(lambda: (done(*res), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def m_display_gamePolicy(self, args, done):
        """0: games see the display's resolution (Native), 1: Steam's default."""
        if len(args) != 1 or args[0] not in (0, 1):
            return done(None, "InvalidValue", "policy")
        prefs = system.load_prefs()
        prefs["game_res"] = args[0]
        system.save_prefs(prefs)
        self.display.invalidate()

        def work():
            if args[0] == 1:
                self.game_env.restore_default()
                r = self.game_env.restore()
            else:
                self.game_env.ensure_default(self._env_state)
                r = self.game_env.apply(list(self.games) + list(shortcuts.shortcut_appids(self.root())))
            GLib.idle_add(lambda: (done({"ok": r.get("error") is None, "changed": len(r.get("changed", [])),
                                          "error": r.get("error")}), False)[1])
        threading.Thread(target=work, daemon=True).start()

    def m_display_goal(self, args, done):
        """0: fluid (the highest resolution at 60 Hz or more the link carries), 1: the highest resolution at any refresh."""
        if len(args) != 1 or args[0] not in (0, 1):
            return done(None, "InvalidValue", "goal")
        prefs = system.load_prefs()
        prefs["display_goal"] = "max_res" if args[0] else "fluid"
        system.save_prefs(prefs)
        self.display.invalidate()

        def work():
            r = {"error": None}
            if prefs.get("game_res", 0) == 0:
                self.game_env.ensure_default(self._env_state)
            GLib.idle_add(lambda: (done({"ok": True, "goal": prefs["display_goal"], "error": r["error"]}), False)[1])
        threading.Thread(target=work, daemon=True).start()

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


class _SteamUIProxy:
    """STONE DISPLAY reaches Steam through the host's current Steam interface (a fake in tests)."""

    def __init__(self, host):
        self.host = host

    def ui_value(self, expression):
        fn = getattr(self.host.steam_ui, "ui_value", None)
        return fn(expression) if fn else None

    def __getattr__(self, name):
        # ui_values (per-page answers and errors) only where the Steam interface offers it
        if name == "ui_values" and hasattr(self.host.steam_ui, "ui_values"):
            return self.host.steam_ui.ui_values
        raise AttributeError(name)

    def run_game(self, game_id):
        return self.host.steam_ui.run_game(game_id)


def frontend_display(snap):
    """What the interface needs to show the display (STONE Home never reads Linux itself)."""
    p = snap.get("primary") or {}
    nat, out = p.get("native_resolution"), snap.get("output")
    rates = p.get("refresh_rates") or []
    return {
        "connected": bool(p), "name": p.get("display_name"), "connection": p.get("connection_type"),
        "connector": p.get("connector"), "native": nat, "output": out, "preferred": snap.get("preferred_mode"),
        "max_hz": max(rates) if rates else None, "rates": rates,
        "vrr": {"supported": p.get("vrr_supported"), "range": p.get("vrr_range"), "active": p.get("vrr_active")},
        "hdr": {"supported": p.get("hdr_supported"), "active": p.get("hdr_active")},
        "game_env": snap.get("game_environment"), "steam_default": snap.get("steam_game_default"),
        "policy": snap.get("game_policy"), "safe": snap.get("safe_display_mode"),
        "steam": snap.get("steam_available"), "steam_error": snap.get("steam_error"), "findings": snap.get("findings", []),
        "link": snap.get("link"), "goal": snap.get("goal"),
    }


STATUS_TEXT = {"UNKNOWN": "Sin datos", "AUTO_GENERATED": "Medido en esta consola", "RECOMMENDED": "Recomendado por STONE",
               "USER_MODIFIED": "Tu configuración", "HARDWARE_VERIFIED": "Verificado en esta consola"}


def frontend_recommended(r):
    """What the page shows next to JUGAR (nothing it could not show honestly)."""
    return {"mode": r["mode"], "label": "STONE RECOMMENDED" if r["mode"] == "recommended" else r["mode"].upper(),
            "summary": games_opt.summary(r["config"]), "why": r["why"], "settings": r["settings"],
            "status": r["status"], "status_text": STATUS_TEXT.get(r["status"], r["status"]),
            "target": r["target"], "output": r["output"], "internal": r["internal"], "trail": r["trail"],
            "stale": r["stale"]}


def frontend_profile(p):
    return {"id": p["id"], "appid": p["appid"], "name": p["name"], "status": p["status"], "layers": p["layers"],
            "errors": p["errors"], "db_version": p["db_version"], "game": p["game"],
            "availability": p["availability"], "stale": p["stale"], "targets": p["targets"],
            "measurements": len(p["measurements"]),
            "next_measurement": games_opt.summary(p["next_measurement"]) if p["next_measurement"] else None,
            "anti_cheat": p["facts"]["anti_cheat"], "injected": (p["facts"]["injected"] or {}).get("kind")}
