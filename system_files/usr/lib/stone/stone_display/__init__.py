"""STONE DISPLAY: what display is connected, what it can do, what Gamescope outputs, and what
resolution environment games get. Part of the STONE system layer; STONE Home asks it through
its host (never Linux directly). See STONE_DISPLAY.md.

  Display.status()            the full model (model.snapshot), cached until the display changes
  Display.invalidate()        drop the cache (DRM hotplug uevent, or a Steam display change)
  GameEnvironment.ensure_default(display)
                              Steam's default game size raised to the display's native one
                              (verified by reading Steam's state back; undone if it did not take)
  GameEnvironment.apply(ids)  the game environment policy on Steam's per-game "Game resolution"
  GameEnvironment.restore()   undo what STONE changed

Event-driven: the cache is dropped on DRM change uevents (GUdev, when present) and when STONE
Home is shown; nothing polls EDID or the screen.
"""

import json
import os
import sys
import threading
import time

from . import drm, link, model, steam, xwayland  # noqa: F401  (stone_display.link is part of the package API)

CHUNK = 150
LOG_PREFIX = "[stone] display:"


def log(msg):
    print(f"{LOG_PREFIX} {msg}", file=sys.stderr, flush=True)


def state_dir():
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"), "stone")


def collect(game_policy="native", use_steam=True, cef=None, goal="fluid"):
    """Gather every source now (no cache) and build the model."""
    kernel = drm.connectors()
    st, err = (steam.display_state(cef) if cef else steam.display_state()) if use_steam else (None, "not asked")
    snap = model.snapshot(kernel, st, xwayland.screens(), game_policy, goal)
    snap["steam_error"] = err
    snap["time"] = int(time.time())
    return snap


class Display:
    def __init__(self, game_policy=lambda: "native", cef=None, on_change=None, goal=lambda: "fluid"):
        self._lock = threading.Lock()
        self._snap = None
        self._sig = None
        self.game_policy = game_policy
        self.goal = goal
        self.cef = cef
        self.on_change = on_change
        self._udev = None
        self._watch()

    def _watch(self):
        """DRM hotplug and mode changes arrive as udev "change" events on the drm subsystem."""
        try:
            import gi
            gi.require_version("GUdev", "1.0")
            from gi.repository import GUdev
        except (ImportError, ValueError):
            return
        try:
            self._udev = GUdev.Client(subsystems=["drm"])
            self._udev.connect("uevent", lambda *a: self._changed("drm uevent"))
        except Exception as e:  # noqa: BLE001  (no hotplug events: the cache is still dropped on demand)
            log(f"no hotplug events ({e})")

    def _changed(self, why):
        self.invalidate()
        if self.on_change:
            self.on_change(why)

    def invalidate(self):
        with self._lock:
            self._snap = None

    def status(self, fresh=False):
        with self._lock:
            if self._snap is not None and not fresh:
                return self._snap
        snap = collect(self.game_policy(), cef=self.cef, goal=self.goal())
        sig = model.signature(snap)
        with self._lock:
            self._snap = snap
            changed, self._sig = sig != self._sig, sig
        if changed:
            log_snapshot(snap)
        return snap


def log_snapshot(snap):
    """A few lines when the display situation changes (not on every query)."""
    p = snap.get("primary")
    if not p:
        log("DISPLAY_DISCONNECTED")
        return
    nat, cur = p.get("native_resolution"), p.get("current_resolution")
    log(f"DISPLAY_CONNECTED {p.get('display_name') or 'unknown'} via {p.get('connection_type')} ({p['connector']})")
    log(f"CAPABILITIES_DETECTED native={_r(nat)} rates={p.get('refresh_rates')} vrr={p.get('vrr_range') or p.get('vrr_supported')} "
        f"hdr={p.get('hdr_supported')}")
    log(f"OUTPUT_MODE {_r(cur)} preferred={_r(snap.get('preferred_mode'))} safe={snap.get('safe_display_mode')}")
    log(f"GAME_ENVIRONMENT {_r(snap.get('game_environment'))} steam_default={_r(snap.get('steam_game_default'))} "
        f"policy={snap.get('game_policy')} xwayland={ {k: _r(v) for k, v in snap.get('xwayland', {}).items()} }")
    for f in snap.get("findings", []):
        if f["level"] != "ok":
            log(f"{f['code']}: {f['text']}")


def _r(m):
    if not m or not m.get("w"):
        return "UNKNOWN"
    return f"{m['w']}x{m['h']}" + (f"@{m['hz']}" if m.get("hz") else "")


class GameEnvironment:
    """Steam decides the resolution environment of each game ("Game resolution" in its
    properties). With "Default", Steam gives games its own default size, which can be smaller than
    the display (e.g. 1920x1080 on a 4K TV). The STONE policy "native" sets "Native" on every game
    whose setting is still "Default", so known and unknown games alike see the display's own
    resolution; a size the person chose is never touched. The ids STONE changed are recorded so
    the policy "steam" can put them back."""

    def __init__(self, cef=None):
        self.cef = cef
        self.path = os.path.join(state_dir(), "game-resolution.json")

    def _doc(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def _load(self):
        try:
            return {int(x) for x in self._doc().get("changed", [])}
        except (ValueError, TypeError):
            return set()

    def _save(self, ids):
        d = self._doc()
        d["changed"] = sorted(ids)
        self._write(d)

    def ensure_default(self, read_state, settle=lambda: time.sleep(1.0)):
        """System-wide part of the policy: Steam's default game size = the display's native size,
        so games launched from anywhere (Steam's own library too) inherit it. APPLY -> VERIFY ->
        keep, or ROLLBACK to the previous value. read_state() -> (steam_state, native) fresh.
        Not retried once Steam showed it does not work that way (the per-game "Native" still does)."""
        doc = self._doc()
        if doc.get("default_method") == "unsupported":
            return {"status": "unsupported"}
        st, native = read_state()
        if not st or not native or not st.get("game_default"):
            return {"status": "unknown"}
        before = st["game_default"]
        if before["w"] * before["h"] >= native["w"] * native["h"]:
            return {"status": "ok", "default": before}
        r, err = self._run(steam.set_default_expression(native["w"], native["h"]))
        if r is None:
            return {"status": "error", "error": err}
        settle()
        st2, _ = read_state()
        after = (st2 or {}).get("game_default")
        if after and (after["w"], after["h"]) == (native["w"], native["h"]):
            doc = self._doc()
            doc["default_before"] = before
            doc["default_method"] = "ok"
            self._write(doc)
            log(f"GAME_ENVIRONMENT_CREATED Steam default game size {before['w']}x{before['h']} -> {after['w']}x{after['h']}")
            return {"status": "changed", "before": before, "default": after}
        self._run(steam.set_default_expression(before["w"], before["h"]))           # ROLLBACK
        doc = self._doc()
        doc["default_method"] = "unsupported"
        self._write(doc)
        log(f"Steam default game size not changed by SetGamescopeInternalResolution (now {_r(after)}): "
            "rolled back; per-game Native only")
        return {"status": "unsupported", "default": after}

    def restore_default(self):
        doc = self._doc()
        before = doc.get("default_before")
        if not before:
            return {"status": "nothing"}
        r, err = self._run(steam.set_default_expression(before["w"], before["h"]))
        if r is None:
            return {"status": "error", "error": err}
        doc.pop("default_before", None)
        self._write(doc)
        log(f"Steam default game size back to {before['w']}x{before['h']}")
        return {"status": "restored", "default": before}

    def _run(self, expr):
        return steam.run_batch(expr, self.cef) if self.cef else steam.run_batch(expr)

    def apply(self, appids):
        ids = sorted({int(a) & 0xFFFFFFFF for a in appids if str(a).lstrip("-").isdigit() and int(a) & 0xFFFFFFFF})
        changed, err = set(), None
        for k in range(0, len(ids), CHUNK):
            r, err = self._run(steam.ensure_native_expression(ids[k:k + CHUNK]))
            if r is None:
                break
            changed.update(int(x) for x in r.get("changed", []))
        if changed:
            self._save(self._load() | changed)
            log(f"GAME_ENVIRONMENT_CREATED Native for {len(changed)} games")
        return {"changed": sorted(changed), "checked": len(ids), "error": err}

    def restore(self):
        ids = self._load()
        if not ids:
            return {"changed": [], "error": None}
        back, err = set(), None
        lst = sorted(ids)
        for k in range(0, len(lst), CHUNK):
            r, err = self._run(steam.restore_default_expression(lst[k:k + CHUNK]))
            if r is None:
                break
            back.update(int(x) for x in r.get("changed", []))
        if err is None:
            self._save(set())
        log(f"game resolution back to Steam's default for {len(back)} games")
        return {"changed": sorted(back), "error": err}
