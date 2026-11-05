"""STONE OPTIMIZER: choose, apply, measure and remember the best experience for one game.

GAME START   SELECT GAME -> DETECT DISPLAY -> LOAD GAME PROFILE -> VALIDATE (invalidation,
             availability, failures) -> APPLY GRAPHICS (launch options, verified) -> APPLY
             PERFORMANCE (stoned per-game profile) -> VERIFY -> LAUNCH (Steam) -> MONITOR
GAME EXIT    SAVE METRICS (measurement launches) -> RESTORE GLOBAL PERFORMANCE -> CLEAN GRAPHICS
             OVERRIDES -> STONE HOME
GAME CRASH   (an exit within ABNORMAL_S of the start, or STONE finding a session that never
             ended) COLLECT STATE -> RESTORE PERFORMANCE -> CLEAN GRAPHICS -> INCREMENT FAILURE
             (graphics and performance counted apart) -> SAFE NEXT LAUNCH

MEASURED PROFILE OPTIMIZATION (no machine learning): a fixed candidate matrix is measured one
launch at a time; every finished measurement is scored (score.py) and the profile's modes are
recomputed from the measurements valid for the current environment. The next launch is LOAD
PROFILE -> VERIFY ENVIRONMENT -> PLAY; nothing is benchmarked again until something relevant
changes (INVALIDATION below).

INVALIDATION (environment(); keys compared with the ones the measurements were made with):
  game_build, proton, injected (HelixSR/OptiScaler files), gpu_backend (40 CU / 24 CU, governor),
  calibration (validated maximum), display (native size, refresh, VRR), engine (score/graphics
  rules) -> measurements STALE: the recommendation falls back to the built-in/safe one and the
  matrix is offered again.  stone_version alone -> re-verify availability, measurements kept.

FALLBACK (graphics):  helixsr -> game_upscaler -> native;  optiscaler -> game_upscaler -> native.
                      Each step is a clean launch: the injected DLL is only ever loaded through
                      STONE's launch option, which is removed before the next launch.
FALLBACK (performance): stoned does its own (Last Known Good -> BALANCED); here a failed run on
                      "performance" drops the next launch to "balanced". Independent of graphics.
"""

import json
import os
import time

from . import detect, graphics, metrics, schema, score, store

ENGINE_VERSION = 1
ABNORMAL_S = 60          # a game gone this soon after it appeared did not end normally
SUSPECT_AFTER = 2        # abnormal exits before a configuration is suspect
START_TIMEOUT_S = 180    # Steam did not start the game: clean up
FALLBACK = {"helixsr": ["game_upscaler", "native"], "optiscaler": ["game_upscaler", "native"],
            "game_upscaler": ["native"], "gamescope": ["native"], "native": []}
STALE_KEYS = ("game_build", "proton", "injected", "gpu_backend", "calibration", "display", "engine")
PRESETS_MATRIX = ("quality", "balanced", "performance")


def release_version():
    path = os.environ.get("STONE_RELEASE_FILE", "/usr/lib/stone-release")
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("STONE_VERSION="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


# ---- inputs ---------------------------------------------------------------------------------
def display_input(snap):
    """What the optimizer needs from STONE DISPLAY. Unknown display -> 60 Hz, no VRR, known False."""
    snap = snap or {}
    p = snap.get("primary") or {}
    out = snap.get("output") or {}
    vr = p.get("vrr_range") or {}
    known = bool(p) and bool(out.get("w"))
    return {"known": known, "output": {"w": out.get("w"), "h": out.get("h")} if known else None,
            "native": p.get("native_resolution"), "hz": (out.get("hz") if known else None) or 60,
            "vrr": {"supported": bool(p.get("vrr_supported")) and known,
                    "range": [vr["min"], vr["max"]] if vr.get("min") and vr.get("max") else None,
                    "active": p.get("vrr_active")},
            "hdr": {"supported": p.get("hdr_supported"), "active": p.get("hdr_active")},
            "modes": len(p.get("modes") or [])}


def perf_input(st, caps=None):
    """What the optimizer needs from STONE PERFORMANCE (stoned GetState/GetCapabilities)."""
    st, caps = st or {}, caps or {}
    rng = st.get("range") or {}
    cu = caps.get("cu40") or {}
    return {"available": bool(st), "profile": st.get("profile"), "safe_mode": bool(st.get("safe_mode")),
            "validated_mhz": rng.get("validated_max") or rng.get("max"), "range": rng,
            "cu40": cu.get("active") if isinstance(cu, dict) else None,
            "governor": (f"{caps.get('driver')}/governor" if caps.get("governor") else caps.get("driver")) if caps else None,
            "profiles": sorted((st.get("profiles") or {}).keys()), "per_game": dict(st.get("per_game") or {})}


def environment(facts, display, perf):
    d = display
    return {"game_build": facts.get("build"), "proton": facts.get("proton"),
            "injected": (facts.get("injected") or {}).get("fingerprint"),
            "gpu_backend": f"{perf.get('governor')}/cu40={perf.get('cu40')}",
            "calibration": perf.get("validated_mhz"),
            "display": (f"{(d.get('native') or {}).get('w')}x{(d.get('native') or {}).get('h')}@{d.get('hz')}"
                        f"/vrr={int(bool(d['vrr']['supported']))}") if d.get("known") else "unknown",
            "engine": ENGINE_VERSION, "stone_version": release_version()}


def stale_reasons(measured_env, env):
    if not measured_env:
        return []
    return [k for k in STALE_KEYS if measured_env.get(k) != env.get(k)]


# ---- decisions ------------------------------------------------------------------------------
def _cfg(path, preset=None, perf="balanced", fps=None, settings=None, filt=None):
    scale = 1.0 if preset in (None, "native_aa") else schema.PRESET_SCALE[preset]
    return schema.clean_config({"graphics": {"path": path, "upscaler_preset": preset,
                                             "internal": {"strategy": "native" if scale >= 1.0 else "upscaled", "scale": scale},
                                             "game_settings": settings},
                                "performance": {"profile": perf}, "frame_target": {"fps": fps},
                                "gamescope": {"scaling_filter": filt}})


def gkey(c):
    g = c["graphics"]
    return f"{g['path']}:{g['upscaler_preset'] or '-'}:{g['internal']['scale']}"


def safe_config(avail, game):
    """No injection, the game's own upscaler at Quality if it has one, BALANCED, no cap."""
    if avail.get("game_upscaler", {}).get("available"):
        return _cfg("game_upscaler", "quality", settings="En el juego: escalado FSR (o XeSS) en Calidad")
    return _cfg("native")


def matrix(profile, avail):
    """Stage 1: every available graphics path × presets at BALANCED. Built-in candidates (the
    reference matrix of the profile database) come first when given. Never: frame generation,
    a performance profile beyond what stoned validated (stoned refuses it anyway)."""
    out, seen = [], set()
    base = list(profile.get("candidates") or [])
    for path in ("native", "game_upscaler", "helixsr", "optiscaler"):
        if not avail.get(path, {}).get("available"):
            continue
        for preset in ((None,) if path == "native" else PRESETS_MATRIX):
            base.append(_cfg(path, preset))
    for c in base:
        if not avail.get(c["graphics"]["path"], {}).get("available") or c["graphics"]["frame_generation"]:
            continue
        k = schema.config_key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def valid_measurements(profile, env):
    if stale_reasons(profile.get("invalidation"), env):
        return []
    return [m for m in profile.get("measurements", []) if m.get("score") and m["score"].get("recommended", {}).get("score") is not None]


def suspect(profile, c):
    f = profile.get("failures", {})
    return f.get("g:" + gkey(c), 0) >= SUSPECT_AFTER or f.get("p:" + c["performance"]["profile"], 0) >= SUSPECT_AFTER


def stage2(profile, avail, env):
    """After stage 1: the two best graphics configurations at SILENT and PERFORMANCE (the
    performance + graphics loop: measure, adjust the GPU range, measure)."""
    ms = valid_measurements(profile, env)
    best = sorted((m for m in ms if m["config"]["performance"]["profile"] == "balanced"),
                  key=lambda m: -(m["score"]["recommended"]["experience"] or 0))[:2]
    out = []
    for m in best:
        for pid in ("silent", "performance"):
            c = schema.clean_config(dict(m["config"], performance={"profile": pid}))
            c["frame_target"] = {"fps": None, "vrr": False}
            out.append(c)
    return out


def next_candidate(profile, avail, env):
    """The next configuration to measure, or None when the matrix is done."""
    done = {schema.config_key(dict(m["config"], frame_target={"fps": None, "vrr": False}))
            for m in valid_measurements(profile, env)}
    for c in matrix(profile, avail) + stage2(profile, avail, env):
        if schema.config_key(c) not in done and not suspect(profile, c) and avail.get(c["graphics"]["path"], {}).get("available"):
            return c
    return None


def recommend(profile, avail, env, display, mode="recommended"):
    """(config, why). Order: user choice (CUSTOM) -> measured best -> profile database -> safe."""
    t = score.targets(display)
    if mode == "custom" and "custom" in profile.get("modes", {}):
        return profile["modes"]["custom"], "your configuration (CUSTOM)"
    if profile.get("source") == "user" and mode in profile.get("modes", {}):
        return profile["modes"][mode], "your configuration"
    ms = [m for m in valid_measurements(profile, env)
          if avail.get(m["config"]["graphics"]["path"], {}).get("available") and not suspect(profile, m["config"])]
    sm = "recommended" if mode == "custom" else mode
    scored = [m for m in ms if m["score"][sm]["score"] is not None]
    if scored:
        top = max(m["score"][sm]["experience"] for m in scored)
        equal = [m for m in scored if m["score"][sm]["experience"] >= top - score.EQUAL_POINTS]
        # EFFICIENCY first (lowest average GPU clock), then HelixSR as the preferred candidate.
        equal.sort(key=lambda m: ((m["metrics"].get("gpu_clock_avg") or 1e9) // 50,
                                  0 if m["config"]["graphics"]["path"] == "helixsr" else 1,
                                  -m["score"][sm]["experience"]))
        best = equal[0]
        c = schema.clean_config(best["config"])
        cap = score.sustained(best["metrics"], display)
        c["frame_target"] = {"fps": cap, "vrr": t["vrr"]}
        return c, f"measured on this console ({len(scored)} runs; experience {best['score'][sm]['experience']})"
    builtin = profile.get("modes", {}).get(mode) or profile.get("modes", {}).get("recommended")
    if builtin and avail.get(builtin["graphics"]["path"], {}).get("available") and not suspect(profile, builtin):
        return builtin, "STONE profile database (not measured on this console)"
    return safe_config(avail, profile.get("game", {})), "safe configuration (no data for this game yet)"


def fallback(config, avail, profile):
    """The next safe graphics path for a config that cannot be used (or failed)."""
    for p in FALLBACK[config["graphics"]["path"]]:
        if avail.get(p, {}).get("available"):
            c = schema.copy_profile(config)
            if p == "native":
                c["graphics"].update({"path": "native", "upscaler_preset": None, "internal": {"strategy": "native", "scale": 1.0},
                                      "game_settings": "En el juego: escalado desactivado (o TAA)"})
            else:
                c["graphics"].update({"path": p, "game_settings": "En el juego: escalado FSR (o XeSS) en el mismo preset"})
            if not suspect(profile, c):
                return c
    return None


def settings_hint(c, game):
    g = c["graphics"]
    if g.get("game_settings"):
        return g["game_settings"]
    preset = {"quality": "Calidad", "balanced": "Equilibrado", "performance": "Rendimiento",
              "ultra_performance": "Ultra rendimiento", "native_aa": "AA nativo"}.get(g["upscaler_preset"], "")
    if g["path"] in ("helixsr", "optiscaler"):
        return f"En el juego: DLSS (o el escalado que HelixSR/OptiScaler reemplaza) en {preset}".strip()
    if g["path"] == "game_upscaler":
        return f"En el juego: FSR (o XeSS) en {preset}".strip()
    if g["path"] == "gamescope":
        return "Steam (···) → Rendimiento → Filtro de escalado; resolución del juego menor que la salida"
    return "En el juego: sin escalado (resolución = salida)"


# ---- the engine -----------------------------------------------------------------------------
class Engine:
    """Everything above with the real (or, in tests, fake) world:
        root()          Steam's folder
        cef             Steam's interface (ui_value, run_game)
        display()       STONE DISPLAY snapshot (stone_display.Display.status)
        perf            .state() / .capabilities() / .set_game_profile(appid, pid) -> (ok, why)
        watch           .running(appid) -> bool  (Steam's reaper for this AppId exists)
    """

    def __init__(self, root, cef, display, perf, watch=None, clock=time.time, log=print):
        self.root, self.cef, self.display_fn, self.perf = root, cef, display, perf
        self.watch = watch or ProcWatch()
        self.clock, self.log = clock, log
        self.lo = graphics.LaunchOptions(cef)
        self.ov = graphics.Overrides(self.lo)
        self.session_path = os.path.join(graphics.state_dir(), "game-session.json")
        self.metrics_root = os.path.join(graphics.state_dir(), "game-metrics")

    # -- session record (survives a crash of STONE Home or of the console) --
    def _session(self):
        try:
            with open(self.session_path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = None
        return d if isinstance(d, dict) else None

    def _save_session(self, d):
        os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
        if d is None:
            try:
                os.remove(self.session_path)
            except FileNotFoundError:
                pass
            return
        tmp = self.session_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.session_path)

    # -- inputs --
    def inputs(self, appid):
        facts = detect.facts(self.root(), appid)
        try:
            disp = display_input(self.display_fn())
        except Exception as e:  # noqa: BLE001  unknown display is a valid state
            self.log(f"[stone] optimized: display unknown ({e!r})")
            disp = display_input(None)
        try:
            pin = perf_input(self.perf.state(), self.perf.capabilities())
        except Exception as e:  # noqa: BLE001
            self.log(f"[stone] optimized: performance unknown ({e!r})")
            pin = perf_input(None)
        return facts, disp, pin

    def profile(self, appid, name=""):
        """getGameProfile: the merged profile + what is true right now."""
        p = store.load(appid, name)
        facts, disp, pin = self.inputs(appid)
        original, _ = self.lo.get(appid)
        avail = graphics.availability(p["game"], facts, original or "")
        env = environment(facts, disp, pin)
        stale = stale_reasons(p.get("invalidation"), env)
        p["facts"], p["display"], p["performance_input"] = facts, disp, pin
        p["availability"], p["environment"], p["stale"] = avail, env, stale
        p["targets"] = score.targets(disp)
        p["next_measurement"] = next_candidate(p, avail, env)
        return p

    def recommended(self, appid, mode="recommended", p=None):
        """getRecommendedExperience: config + why + what the person sets in the game."""
        if mode not in schema.MODES:
            raise schema.ProfileError("mode")
        p = p or self.profile(appid)
        c, why = recommend(p, p["availability"], p["environment"], p["display"], mode)
        owned = why.startswith(("measured", "your"))
        trail = []
        if not p["availability"].get(c["graphics"]["path"], {}).get("available"):
            trail.append(f"{c['graphics']['path']}: {p['availability'].get(c['graphics']['path'], {}).get('reason')}")
            fb = fallback(c, p["availability"], p)
            c, why = (fb, why + " → fallback") if fb else (safe_config(p["availability"], p["game"]), "safe configuration")
        last = p["safety"]["next_safe"]
        if last and last == gkey(c):
            fb = fallback(c, p["availability"], p) or safe_config(p["availability"], p["game"])
            trail.append(f"last start of {last} ended abnormally: safe launch")
            c, why = fb, "safe launch after an abnormal exit"
        if p["safety"]["next_safe_perf"] == c["performance"]["profile"] and c["performance"]["profile"] != "balanced":
            c = schema.copy_profile(c)
            c["performance"]["profile"] = "balanced"
            trail.append("performance: back to BALANCED after an abnormal exit")
        if p["performance_input"]["safe_mode"]:
            trail.append("performance: stoned in Safe Mode (its own profile applies)")
        return {"mode": mode, "config": c, "why": why, "trail": trail, "perf_owned": owned, "status": p["status"],
                "settings": settings_hint(c, p["game"]), "target": p["targets"]["targets"][mode],
                "output": (p["display"] or {}).get("output"),
                "internal": _internal(c, (p["display"] or {}).get("output")), "stale": p["stale"]}

    def modes(self, appid):
        """getAvailableModes."""
        p = self.profile(appid)
        out = []
        for m in schema.MODES:
            if m == "custom" and "custom" not in p["modes"]:
                out.append({"mode": m, "available": False, "why": "no CUSTOM configuration yet"})
                continue
            r = self.recommended(appid, m, p)
            out.append({"mode": m, "available": True, "summary": summary(r["config"]), "why": r["why"],
                        "target": r["target"], "default": m == "recommended"})
        return out

    def apply(self, appid, mode="recommended", config=None):
        """applyGameProfile: CUSTOM saves the person's own configuration (USER_MODIFIED)."""
        if mode == "custom" and config is None:
            if "custom" not in store.load(appid)["modes"]:
                raise schema.ProfileError("no CUSTOM configuration yet")
        elif mode == "custom":
            c = schema.clean_config(config)
            p, _ = store.read_layer("user", appid)
            p = p or dict(schema.empty_profile(appid), source="user")
            p["modes"]["custom"] = c
            p["status"] = "USER_MODIFIED"
            p["revision"] = p.get("revision", 0) + 1
            store.write_layer("user", p)
        prefs = _mode_prefs()
        prefs[str(appid)] = mode
        _save_mode_prefs(prefs)
        return {"ok": True, "mode": mode}

    # -- components: only on the person's request, never for anti-cheat / online games --
    def components(self):
        from . import install
        return {"components": install.status()}

    def fetch_component(self, kind):
        from . import install
        try:
            return {"ok": True, **install.fetch(kind, log=self.log)}
        except install.InstallError as e:
            raise schema.ProfileError(str(e)) from e
        except OSError as e:
            raise schema.ProfileError(f"no se pudo descargar ({e.__class__.__name__}): ¿hay internet?") from e

    def install_plan(self, appid, kind="optiscaler"):
        from . import install
        p = self.profile(appid)
        if kind == "helixsr":
            r = install.helixsr_command()
            return r
        r = install.install_plan(appid, p["facts"], p["game"])
        return {k: v for k, v in r.items() if not k.startswith("_")} | {"by_stone": bool(install.installed_by_stone(appid))}

    def install_image(self, appid, kind="optiscaler"):
        from . import install
        if kind != "optiscaler":
            raise schema.ProfileError("HelixSR: usa el instalador oficial (stone-game components)")
        p = self.profile(appid)
        r = install.install_optiscaler(appid, p["facts"], p["game"])
        if r.get("ok"):
            self.log(f"[stone] optimized: OptiScaler installed in {appid} ({r['folder']})")
        return r

    def uninstall_image(self, appid):
        from . import install
        return install.uninstall(appid)

    # -- guided optimization: the measurement matrix, one launch at a time --
    def optimize_status(self, appid):
        """How far the measurement of this game is, and what the next launch would be."""
        p = self.profile(appid)
        rows = matrix(p, p["availability"]) + stage2(p, p["availability"], p["environment"])
        done_keys = {schema.config_key(dict(m["config"], frame_target={"fps": None, "vrr": False}))
                     for m in (p["measurements"] if not p["stale"] else [])}
        done = sum(1 for c in rows if schema.config_key(c) in done_keys)
        nxt = p["next_measurement"]
        rec = self.recommended(appid, "recommended", p)
        return {"done": done, "total": len(rows), "next": summary(nxt) if nxt else None,
                "benchmark": (p["game"] or {}).get("benchmark"), "stale": p["stale"],
                "recommended": summary(rec["config"]), "status": p["status"], "measured": len(p["measurements"])}

    # -- image enhancement: the person's own, explicit choice (never HelixSR unless chosen) --
    IMAGE_LABELS = {"native": "Sin mejora (nativo)", "game_upscaler": "FSR del juego", "gamescope": "FSR de pantalla (Gamescope)",
                    "helixsr": "HelixSR", "optiscaler": "OptiScaler"}
    IMAGE_PRESETS = ("quality", "balanced", "performance", "ultra_performance")

    def image_options(self, appid):
        """What the person can pick for the image of this game, and what is picked now."""
        p = self.profile(appid)
        mode = self.chosen_mode(appid)
        cur = self.recommended(appid, mode, p)["config"]["graphics"]
        out = [{"key": "auto", "label": "STONE decide", "available": True, "reason": None,
                "selected": mode != "custom"}]
        for key in ("native", "game_upscaler", "gamescope", "helixsr", "optiscaler"):
            a = p["availability"].get(key, {"available": False, "reason": "unknown"})
            if key in ("helixsr", "optiscaler") and not a.get("available") and \
                    ("not installed" in (a.get("reason") or "") or "installed instead" in (a.get("reason") or "")):
                continue                                          # not installed: no point listing it
            out.append({"key": key, "label": self.IMAGE_LABELS[key], "available": bool(a.get("available")),
                        "reason": a.get("reason"), "guidance_only": bool(a.get("guidance_only")),
                        "selected": mode == "custom" and cur["path"] == key})
        from . import install
        return {"mode": mode, "path": cur["path"], "preset": cur["upscaler_preset"], "choices": out,
                "by_stone": bool(install.installed_by_stone(appid)),
                "presets": list(self.IMAGE_PRESETS)}

    def set_image(self, appid, key, preset=None):
        """Apply the person's choice. "auto" goes back to STONE RECOMMENDED. Anything unavailable is refused."""
        if key == "auto":
            return self.apply(appid, "recommended")
        if key not in self.IMAGE_LABELS or (preset is not None and preset not in self.IMAGE_PRESETS):
            raise schema.ProfileError("image choice")
        p = self.profile(appid)
        a = p["availability"].get(key, {})
        if not a.get("available"):
            raise schema.ProfileError(f"{key}: {a.get('reason') or 'not available'}")
        c = schema.copy_profile(self.recommended(appid, self.chosen_mode(appid), p)["config"])
        g = c["graphics"]
        g["path"] = key
        if key == "native":
            g["upscaler_preset"], g["internal"] = None, {"strategy": "native", "scale": 1.0}
        else:
            preset = preset or g["upscaler_preset"] or "quality"
            if preset == "native_aa":
                preset = "quality"
            g["upscaler_preset"] = preset
            g["internal"] = {"strategy": "upscaled", "scale": round(schema.PRESET_SCALE[preset], 3)}
            c["gamescope"]["scaling_filter"] = "fsr" if key == "gamescope" else c["gamescope"]["scaling_filter"]
        return self.apply(appid, "custom", c)

    def restore_recommended(self, appid):
        """restoreRecommended: the person's own choices go; measurements stay."""
        removed = store.delete_layer("user", appid)
        prefs = _mode_prefs()
        prefs.pop(str(appid), None)
        _save_mode_prefs(prefs)
        return {"ok": True, "removed_user_profile": removed}

    def chosen_mode(self, appid):
        m = _mode_prefs().get(str(appid), "recommended")
        return m if m in schema.MODES else "recommended"

    # -- launch / exit --
    def prepare(self, appid, mode=None, measure=None):
        """Everything before LAUNCH. measure: None or {"config": CONFIG|"next", "delay": s, "duration": s}."""
        appid = str(appid)
        self.recover()
        mode = mode or self.chosen_mode(appid)
        p = self.profile(appid)
        rec = self.recommended(appid, mode, p)
        c = rec["config"]
        mstate = None
        if measure:
            mc = p["next_measurement"] if measure.get("config") in (None, "next") else schema.clean_config(measure["config"])
            if mc is None:
                return {"ok": False, "error": "nothing left to measure (matrix done)"}
            if not p["availability"].get(mc["graphics"]["path"], {}).get("available"):
                return {"ok": False, "error": p["availability"].get(mc["graphics"]["path"], {}).get("reason")}
            c = mc
            folder = os.path.join(self.metrics_root, appid)
            os.makedirs(folder, exist_ok=True)
            mstate = {"folder": folder, "before": sorted(os.listdir(folder)), "delay": int(measure.get("delay", 90)), "duration": int(measure.get("duration", 120))}
        env = graphics.env_for(c, p["facts"], metrics.mangohud_env(mstate["folder"], mstate["delay"], mstate["duration"]) if mstate else None)
        if env is None:
            return {"ok": False, "error": "graphics path not possible"}
        applied, err = self.ov.apply(appid, env, schema.config_key(c))
        graphics_ok = applied is not None
        if not graphics_ok:                                # SAFE: launch as the person left it
            self.log(f"[stone] optimized: graphics not applied for {appid}: {err}; launching unchanged")
        # PERFORMANCE: a per-game profile the person chose in STONE Performance is kept as it is;
        # STONE sets one only for a measured (or the person's CUSTOM) configuration, never for a
        # guess, and takes it away again when the game ends.
        pin = p["performance_input"]
        manual = (pin.get("per_game") or {}).get(appid)
        pid, perf_ok, perf_err, perf_set = None, True, None, False
        if manual:
            pid = manual
        elif (measure or rec["perf_owned"]) and pin["available"] and not pin["safe_mode"]:
            pid = c["performance"]["profile"]
            perf_ok, perf_err = self.perf.set_game_profile(appid, pid)
            if not perf_ok and pid != "balanced":
                pid = "balanced"
                perf_ok, perf_err = self.perf.set_game_profile(appid, pid)
            perf_set = perf_ok
            pid = pid if perf_ok else None
        sess = {"appid": appid, "mode": mode, "config": c, "key": schema.config_key(c), "gkey": gkey(c),
                "t_prepared": self.clock(), "t_started": None, "measure": mstate, "graphics_applied": graphics_ok,
                "perf_profile": pid, "perf_set": perf_set, "display": p["display"], "environment": p["environment"],
                "target": rec["target"]}
        self._save_session(sess)
        return {"ok": True, "config": c, "settings": settings_hint(c, p["game"]), "graphics_applied": graphics_ok,
                "graphics_error": err if not graphics_ok else None, "performance": pid or "global",
                "performance_error": perf_err if not perf_ok else None, "why": rec["why"] if not measure else "measurement",
                "trail": rec["trail"], "measure": mstate}

    def launch(self, appid, mode=None, measure=None, run=None):
        """launchOptimized: prepare -> launch through Steam. run(appid) -> status string."""
        r = self.prepare(appid, mode, measure)
        if not r.get("ok"):
            return r
        st = (run or (lambda a: self.cef.run_game(int(a))))(appid)
        r["launch"] = st
        if not str(st).endswith(": ok") and not str(st) == "ok":
            self.log(f"[stone] optimized: Steam did not start {appid} ({st}); cleaning up")
            self.finish(appid, reason="not started")
            r["ok"] = False
            r["error"] = st
        return r

    def tick(self):
        """MONITOR (called every few seconds): start and end of the prepared game."""
        s = self._session()
        if not s:
            return None
        running = self.watch.running(s["appid"])
        now = self.clock()
        if running and not s.get("t_started"):
            s["t_started"] = now
            self._save_session(s)
            self.log(f"[stone] optimized: {s['appid']} started with {s['key']}")
            return "started"
        if not running and s.get("t_started"):
            return self.finish(s["appid"], reason="exit")
        if not running and not s.get("t_started") and now - s["t_prepared"] > START_TIMEOUT_S:
            return self.finish(s["appid"], reason="not started")
        return None

    def finish(self, appid, reason="exit"):
        """GAME EXIT / GAME CRASH: save -> restore performance -> clean graphics."""
        s = self._session()
        if not s or s["appid"] != str(appid):
            return None
        dur = (self.clock() - s["t_started"]) if s.get("t_started") else None
        abnormal = reason == "lost" or (reason == "exit" and dur is not None and dur < ABNORMAL_S)
        result = {"appid": s["appid"], "reason": reason, "seconds": round(dur, 1) if dur is not None else None,
                  "abnormal": abnormal, "key": s["key"]}
        auto, _ = store.read_layer("auto", s["appid"])
        merged = store.load(s["appid"])
        auto = auto or dict(schema.empty_profile(s["appid"], merged.get("name", "")), source="auto")
        changed = False
        if s.get("measure") and reason == "exit" and not abnormal:
            log = metrics.latest_log(s["measure"]["folder"], exclude=s["measure"].get("before", []))
            m = metrics.summarize(log) if log else {"error": "MangoHud wrote no log"}
            result["metrics"] = m
            if not m.get("error"):
                self._add_measurement(auto, s, m)
                changed = True
        if abnormal:
            # Graphics and performance are blamed (and rolled back) apart: graphics only for what
            # STONE injected, performance only for a profile above BALANCED that STONE set.
            f = auto.setdefault("failures", {})
            g_blame = s["config"]["graphics"]["path"] in graphics.INJECTED
            p_blame = bool(s.get("perf_set")) and s["perf_profile"] != "balanced"
            if g_blame or not p_blame:
                f["g:" + s["gkey"]] = f.get("g:" + s["gkey"], 0) + 1
            if g_blame:
                auto["safety"]["next_safe"] = s["gkey"]
            if p_blame:
                f["p:" + s["perf_profile"]] = f.get("p:" + s["perf_profile"], 0) + 1
                auto["safety"]["next_safe_perf"] = s["perf_profile"]
            changed = True
            result["collected"] = {"config": s["config"], "graphics_applied": s["graphics_applied"],
                                   "performance": s.get("perf_profile"), "environment": s.get("environment"),
                                   "blamed": [x for x, b in (("graphics", g_blame), ("performance", p_blame)) if b]}
        elif reason == "exit" and (auto["safety"]["next_safe"] or auto["safety"]["next_safe_perf"]):
            auto["safety"] = {"next_safe": None, "next_safe_perf": None}   # one clean run: back to normal
            changed = True
        if changed:
            self._save_auto(auto)
        # RESTORE GLOBAL PERFORMANCE (stoned also goes back to the global profile when the game ends)
        if s.get("perf_set"):
            ok, err = self.perf.set_game_profile(s["appid"], "global")
            result["performance_restored"] = ok if ok else f"error: {err}"
        # CLEAN GRAPHICS OVERRIDES
        result["graphics_restored"] = self.ov.restore(s["appid"])
        self._save_session(None)
        self.log(f"[stone] optimized: {s['appid']} {reason}{' (abnormal)' if abnormal else ''}: "
                 f"graphics {result['graphics_restored']}")
        return result

    def recover(self):
        """At STONE Home's start (and before every launch): a session that never ended is a crash
        of STONE Home or of the console; overrides left behind are put back."""
        s = self._session()
        out = {}
        if s and not self.watch.running(s["appid"]):
            out["session"] = self.finish(s["appid"], reason="lost" if s.get("t_started") else "not started")
        left = self.ov.restore_all()
        if left:
            out["overrides"] = left
        return out

    def status(self):
        """getOptimizationStatus."""
        s = self._session()
        if not s:
            return {"active": False}
        return {"active": True, "appid": s["appid"], "mode": s["mode"], "config": summary(s["config"]),
                "running": bool(s.get("t_started")), "measuring": bool(s.get("measure")),
                "graphics_applied": s["graphics_applied"], "performance": s.get("perf_profile"), "target": s.get("target")}

    def rate(self, appid, index, rating):
        """The person's verdict on the image of one measurement (1..5); replaces the quality prior."""
        auto, _ = store.read_layer("auto", appid)
        if not auto or not -len(auto["measurements"]) <= index < len(auto["measurements"]) or rating not in range(1, 6):
            raise schema.ProfileError("measurement or rating")
        m = auto["measurements"][index]
        m["image_rating"] = rating
        self._rescore(auto)
        self._save_auto(auto)
        return m

    def verify(self, appid):
        """HARDWARE VERIFIED: the person played the recommended configuration on this console and
        confirms it. Allowed only if it was measured here, in the current environment, without
        failures. Never set by a test or by STONE on its own."""
        p = self.profile(appid)
        c, why = recommend(p, p["availability"], p["environment"], p["display"], "recommended")
        if not why.startswith("measured on this console") or p["stale"] or suspect(p, c):
            raise schema.ProfileError("only a configuration measured on this console, still valid, can be verified")
        auto, _ = store.read_layer("auto", appid)
        auto["status"] = "HARDWARE_VERIFIED"
        auto["verified"] = {"time": int(self.clock()), "config": schema.config_key(c), "environment": p["environment"]}
        self._save_auto(auto, keep_status=True)
        return {"ok": True, "status": "HARDWARE_VERIFIED", "config": summary(c)}

    # -- helpers --
    def _add_measurement(self, auto, s, m):
        if stale_reasons(auto.get("invalidation"), s["environment"]):
            auto["measurements"] = []                      # measured under another environment
            auto["status"] = "AUTO_GENERATED"
        auto["invalidation"] = s["environment"]
        auto["status"], auto["verified"] = "AUTO_GENERATED", None   # a verification was of the old data
        auto["measurements"].append({"config": s["config"], "key": s["key"], "metrics": m, "time": int(self.clock()),
                                     "display": s["display"], "image_rating": None,
                                     "output": (s["display"] or {}).get("output"),
                                     "internal": _internal(s["config"], (s["display"] or {}).get("output"))})
        self._rescore(auto)

    def _rescore(self, auto):
        disp = (auto["measurements"][-1]["display"] if auto["measurements"] else None) or display_input(None)
        t = score.targets(disp)["targets"]
        clocks = [m["metrics"].get("gpu_clock_avg") for m in auto["measurements"] if m["metrics"].get("gpu_clock_avg")]
        ref = min(clocks) if clocks else None
        for m in auto["measurements"]:
            m["score"] = {mode: score.score(m["metrics"], m["config"], mode, t[mode], ref, m.get("image_rating"))
                          for mode in ("recommended", "quality", "performance")}

    def _save_auto(self, auto, keep_status=False):
        if not keep_status and auto.get("status") != "HARDWARE_VERIFIED":
            auto["status"] = "AUTO_GENERATED" if auto.get("measurements") else auto.get("status", "UNKNOWN")
        if auto.get("status") == "HARDWARE_VERIFIED" and not keep_status:
            v = auto.get("verified") or {}
            if stale_reasons(v.get("environment"), auto.get("invalidation")):
                auto["status"] = "AUTO_GENERATED"
        auto["revision"] = auto.get("revision", 0) + 1
        modes = {}
        disp = auto["measurements"][-1]["display"] if auto.get("measurements") else None
        if disp:
            p = dict(auto, source="auto", modes={})
            avail = {k: {"available": True} for k in schema.PATHS}
            for mode in ("recommended", "quality", "performance"):
                c, why = recommend(p, avail, auto["invalidation"], disp, mode)
                if why.startswith("measured"):
                    modes[mode] = c
        auto["modes"] = modes
        return store.write_layer("auto", auto)


def _internal(c, output):
    if not output or not output.get("w"):
        return None
    s = c["graphics"]["internal"]["scale"]
    return {"w": int(round(output["w"] * s)), "h": int(round(output["h"] * s)), "expected": True}


def summary(c):
    g = c["graphics"]
    name = {"native": "Nativo", "game_upscaler": "FSR del juego", "helixsr": "HelixSR", "optiscaler": "OptiScaler",
            "gamescope": "Gamescope"}[g["path"]]
    preset = {"quality": "Calidad", "balanced": "Equilibrado", "performance": "Rendimiento",
              "ultra_performance": "Ultra rendimiento", "native_aa": "AA nativo"}.get(g["upscaler_preset"])
    perf = {"silent": "Silencioso", "balanced": "Equilibrado", "performance": "Rendimiento"}[c["performance"]["profile"]]
    fps = c["frame_target"]["fps"]
    return " · ".join(x for x in (name + (f" {preset}" if preset else ""), f"GPU {perf}", f"{fps} FPS" if fps else None) if x)


def _prefs_path():
    return os.path.join(store.data_dir(), "modes.json")


def _mode_prefs():
    try:
        with open(_prefs_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_mode_prefs(d):
    os.makedirs(os.path.dirname(_prefs_path()), exist_ok=True)
    with open(_prefs_path(), "w") as f:
        json.dump(d, f)


class ProcWatch:
    """Is Steam running this AppId right now? (its `reaper SteamLaunch AppId=<id>` process)."""

    def running(self, appid):
        root = os.path.join(os.environ.get("STONE_SYSFS_ROOT", "/"), "proc")
        try:
            pids = [p for p in os.listdir(root) if p.isdigit()]
        except OSError:
            return False
        for pid in pids:
            try:
                with open(os.path.join(root, pid, "cmdline"), "rb") as f:
                    argv = [x.decode("utf-8", "replace") for x in f.read(4096).split(b"\0") if x]
            except OSError:
                continue
            if argv and os.path.basename(argv[0]) == "reaper" and "SteamLaunch" in argv[1:3] and f"AppId={appid}" in argv[1:6]:
                return True
        return False
