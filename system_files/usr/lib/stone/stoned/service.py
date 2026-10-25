"""stoned: the STONE System Service.

D-Bus name org.stonemachine.Performance1 on the system bus (STONE's own reverse-domain
name; renaming it later means updating the bus policy, polkit actions and stone_home/bridge.py
together). Runs as the unprivileged `stoned`
user. Every method has fixed argument types; none takes a path, a command or a file
name. Write methods are authorized by polkit for the active local session.

The UI shows what this service returns, which is always read back from the governor:
ACTUAL STATE > REQUESTED STATE.
"""

import os
import socket
import sys
import time

from gi.repository import Gio, GLib

from . import state as S
from . import profiles as P
from . import stability
from . import recover as recover_mod
from .telemetry import Telemetry
from .thermal import ThermalWatch
from . import games as games_mod
from .backend_bc250 import BC250Backend
from .errors import (StoneError, PERMISSION_DENIED, INVALID_VALUE, UNSUPPORTED, NOT_VALIDATED, BUSY,
                     ROLLBACK_COMPLETE, PREFIX, SAFE_MODE as SAFE_MODE_ERR)

NAME = "org.stonemachine.Performance1"
PATH = "/org/stonemachine/Performance1"
IFACE = "org.stonemachine.Performance1"
HOME_STABLE_S = 60

XML = f"""
<node>
  <interface name="{IFACE}">
    <method name="GetCapabilities"><arg type="a{{sv}}" direction="out"/></method>
    <method name="GetState"><arg type="a{{sv}}" direction="out"/></method>
    <method name="GetMetrics"><arg type="a{{sv}}" direction="out"/></method>
    <method name="HomeReady"><arg type="a{{sv}}" direction="out"/></method>
    <method name="SetProfile"><arg type="s" name="profile" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="StartTest">
      <arg type="u" name="slot" direction="in"/><arg type="a{{sv}}" name="values" direction="in"/>
      <arg type="s" name="level" direction="in"/><arg type="a{{sv}}" direction="out"/>
    </method>
    <method name="CancelTest"><arg type="a{{sv}}" direction="out"/></method>
    <method name="MeasureBaseline"><arg type="s" name="level" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="SaveTestResult"><arg type="a{{sv}}" direction="out"/></method>
    <method name="DiscardTestResult"><arg type="a{{sv}}" direction="out"/></method>
    <method name="SetSessionProfile"><arg type="s" name="profile" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="RestoreDefaults"><arg type="a{{sv}}" direction="out"/></method>
    <method name="EnterSafeMode"><arg type="a{{sv}}" direction="out"/></method>
    <method name="ExitSafeMode"><arg type="a{{sv}}" direction="out"/></method>
    <method name="SetGameProfile"><arg type="s" name="appid" direction="in"/><arg type="s" name="profile" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="GameStarted"><arg type="s" name="appid" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="GameEnded"><arg type="s" name="appid" direction="in"/><arg type="b" name="normal" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <signal name="StateChanged"><arg type="a{{sv}}"/></signal>
    <signal name="TestFinished"><arg type="a{{sv}}"/></signal>
    <method name="Subscribe"><arg type="s" name="context" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="Unsubscribe"><arg type="a{{sv}}" direction="out"/></method>
    <signal name="Metrics"><arg type="a{{sv}}"/></signal>
    <signal name="ThermalEvent"><arg type="a{{sv}}"/></signal>
  </interface>
</node>"""

# polkit actions (see org.stonemachine.performance.policy)
ACT_CHANGE = "org.stonemachine.performance.change"
ACT_TUNE = "org.stonemachine.performance.tune"
WRITE_METHODS = {"HomeReady": ACT_CHANGE, "SetProfile": ACT_CHANGE, "RestoreDefaults": ACT_CHANGE,
                 "SetGameProfile": ACT_CHANGE, "GameStarted": ACT_CHANGE, "GameEnded": ACT_CHANGE,
                 "StartTest": ACT_TUNE, "CancelTest": ACT_CHANGE, "SaveTestResult": ACT_TUNE,
                 "DiscardTestResult": ACT_CHANGE, "SetSessionProfile": ACT_CHANGE,
                 "EnterSafeMode": ACT_CHANGE, "ExitSafeMode": ACT_CHANGE, "MeasureBaseline": ACT_TUNE}


def to_variant(v):
    if isinstance(v, bool):
        return GLib.Variant("b", v)
    if isinstance(v, int):
        return GLib.Variant("x", v)
    if isinstance(v, float):
        return GLib.Variant("d", v)
    if isinstance(v, str):
        return GLib.Variant("s", v)
    if isinstance(v, dict):
        return GLib.Variant("a{sv}", {str(k): to_variant(x) for k, x in v.items() if x is not None})
    if isinstance(v, (list, tuple)):
        return GLib.Variant("av", [to_variant(x) for x in v if x is not None])
    raise TypeError(type(v))


def sd_notify(msg):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.send(msg.encode())
    except OSError:
        pass


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Service:
    def __init__(self, bus, store=None, backend=None):
        self.bus = bus
        self.store = store or S.Store()
        self.backend = backend or BC250Backend(bus)
        self.st = self.store.load()
        self.caps = self.backend.capabilities()
        self.applied = None            # profile id stoned last applied, or None (governor factory)
        self.run = None                # running stability test
        self.run_timer = 0
        self.game = None               # {"appid", "profile"}
        self.rollback_notice = None
        self.home_ready = False
        self.pending = None            # passed test waiting for SAVE (memory only: lost = nothing saved)
        self.telemetry = Telemetry(self.backend, self._emit_metrics)
        self.thermal = ThermalWatch(self._thermal_reduce, self._thermal_recover, self._thermal_event)
        self.thermal_reduced = False
        self.thermal_notice = None
        self._thermal_timer = GLib.timeout_add(int(self.thermal.interval_s() * 1000), self._thermal_tick)
        # Game sessions come from Steam (games.py). "dbus" lets a caller announce them:
        # test harness and prototype only, never set by the systemd unit.
        self.game_source = os.environ.get("STONE_GAME_SOURCE", "steam")
        self.steam = None
        self._recover_from_service_crash()
        if self.game_source == "steam":
            self.steam = games_mod.SteamWatch(self._game_started, self._game_ended)
            self.steam.scan()          # a game already running after a service restart
            GLib.timeout_add_seconds(games_mod.POLL_S, lambda: (self.steam.scan(), True)[1])

    # ---- startup -------------------------------------------------------------------
    def _recover_from_service_crash(self):
        """stoned restarted in the middle of a session (not a reboot): if a test was
        running, its draft values may still be in the governor. Put back the last known
        good configuration."""
        trial = self.store.get_flag(S.FLAG_TRIAL)
        if trial is None:
            return
        log("stoned: test interrupted by a service restart, restoring last known good")
        try:
            self._apply_profile(self.st["last_known_good"])
            self.rollback_notice = ROLLBACK_COMPLETE
        except StoneError as e:
            self.rollback_notice = e.code
        self.store.clear_flag(S.FLAG_TRIAL)
        self._record_test({"slot": S.slot_index(trial.get("profile")), "passed": False,
                           "reason": "interrupted", "time": int(time.time())})

    # ---- helpers ----------------------------------------------------------------------
    def _save(self):
        self.store.save(self.st)
        self.st = self.store.load()

    def _apply_profile(self, pid):
        values = P.resolve(pid, self.st)
        if self.thermal_reduced and values["max"] > P.FACTORY["silent"]["max"]:
            # The thermal watch holds SILENT until the GPU cools; the profile comes back then.
            return self._actual()
        actual = self.backend.apply(values)
        self.applied = pid
        return actual

    def _actual(self):
        try:
            return self.backend.actual()
        except StoneError as e:
            return {"error": e.code}

    def _slot_view(self, k):
        s = self.st["slots"][k]
        return None if s is None else dict(s, id=f"c{k + 1}")

    def state(self):
        return {
            "profile": self.st["profile"],
            "applied": self.applied or "factory",
            "provisional": self.store.get_flag(S.FLAG_PROVISIONAL) is not None,
            "last_known_good": self.st["last_known_good"],
            "safe_mode": self.st["safe_mode"],
            "safe_reason": self.st["safe_reason"],
            "fail_count": self.st["fail_count"],
            "last_failure": self.st["last_failure"],
            "slots": [self._slot_view(k) or {"id": f"c{k + 1}", "state": "empty"} for k in range(S.SLOT_COUNT)],
            "per_game": self.st["per_game"],
            "game": self.game,
            "testing": self._test_view(),
            "pending_result": self.pending and {"slot": self.pending["slot"], "level": self.pending["level"],
                                                "values": self.pending["values"]},
            "notice": self.rollback_notice,
            "actual": self._actual(),
            "tests": self.st["tests"][-3:],
            "thermal": dict(self.thermal.view(), reduced=self.thermal_reduced, last=self.thermal_notice),
            "telemetry": self.telemetry.status(),
            "games": self.steam.status() if self.steam else {"source": self.game_source},
        }

    def _test_view(self):
        if not self.run:
            return None
        return {"slot": self.run.slot, "level": self.run.level, "seconds": round(self.run.clock() - self.run.t0),
                "duration": self.run.duration, "max_temp_c": self.run.max_temp}

    def _changed(self):
        self.bus.emit_signal(None, PATH, IFACE, "StateChanged", GLib.Variant.new_tuple(to_variant(self.state())))

    def _record_test(self, result):
        self.st["tests"] = (self.st["tests"] + [result])[-10:]
        self._save()

    # ---- methods ------------------------------------------------------------------------
    def GetCapabilities(self):
        c = dict(self.caps)
        c["gpu_range"] = list(c["gpu_range"])
        c["max_range"] = [max(P.STONE_MAX[0], c["gpu_range"][0]), min(P.STONE_MAX[1], c["gpu_range"][1])] if c["governor"] else []
        c["min_range"] = [max(P.STONE_MIN[0], c["gpu_range"][0]), min(P.STONE_MIN[1], c["gpu_range"][1])] if c["governor"] else []
        c["temp_range"] = list(P.STONE_TEMP)
        c["stability_test"] = stability.available() and c["governor"]
        c["advanced"] = c["governor"]     # CUSTOM and advanced need the full chain
        c.pop("initial_range", None)
        return c

    def GetState(self):
        return self.state()

    def GetMetrics(self):
        return self.telemetry.sample()

    # ---- telemetry and thermal watch ----------------------------------------------------
    def Subscribe(self, sender, context):
        if context not in ("performance_center", "overlay"):
            raise StoneError(INVALID_VALUE, "context must be performance_center or overlay")
        return self.telemetry.subscribe(sender, context)

    def Unsubscribe(self, sender):
        return self.telemetry.unsubscribe(sender)

    def _emit_metrics(self, m):
        self.bus.emit_signal(None, PATH, IFACE, "Metrics", GLib.Variant.new_tuple(to_variant(m)))

    def _thermal_tick(self):
        last = self.telemetry.last
        fresh = last is not None and time.time() - last["t"] < self.thermal.interval_s()
        gpu = last["gpu_temp_c"] if fresh else self.backend.gpu_temp()
        fan = last["fan_rpm"] if fresh else self.backend._val("fan_rpm")
        before = self.thermal.interval_s()
        self.thermal.update(gpu, fan)
        if self.thermal.interval_s() != before:
            self._thermal_timer = GLib.timeout_add(int(self.thermal.interval_s() * 1000), self._thermal_tick)
            return False
        return True

    def _effective(self):
        return (self.game or {}).get("profile") or self.st["profile"]

    def _thermal_reduce(self, reason):
        """Lower only. SILENT is applied temporarily; the user's choice is kept, except a
        CUSTOM slot in use, which loses its validation (it ran too hot)."""
        if self.run:
            self.CancelTest()
        pid = self._effective()
        k = S.slot_index(pid)
        if k is not None and self.st["slots"][k] is not None:
            if self.st["slots"][k]["state"] == S.SLOT_VALIDATED:
                self.st["slots"][k]["state"] = S.SLOT_TESTED
            if self.st["last_known_good"] == pid:
                self.st["last_known_good"] = S.DEFAULT_PROFILE
            if self.st["profile"] == pid:
                self.st["profile"] = S.DEFAULT_PROFILE
            if self.game and self.game["profile"] == pid:
                self.game["profile"] = self.st["profile"]
            self.store.clear_flag(S.FLAG_PROVISIONAL)
        self.thermal_reduced = True
        try:
            self.backend.apply(dict(P.FACTORY["silent"]))
            self.applied = "thermal"
        except StoneError as e:
            log(f"stoned: thermal reduce could not apply SILENT: {e.code} {e.message}")
        self._save()
        self._changed()

    def _thermal_recover(self):
        self.thermal_reduced = False
        try:
            self._apply_profile(self._effective())
        except StoneError as e:
            log(f"stoned: profile not re-applied after cooling: {e.code}")
        self._changed()

    def _thermal_event(self, ev):
        ev = dict(ev, time=int(time.time()))
        self.thermal_notice = ev
        self.bus.emit_signal(None, PATH, IFACE, "ThermalEvent", GLib.Variant.new_tuple(to_variant(ev)))

    def HomeReady(self):
        """Home is on screen and the user can act. Only now is the saved profile applied,
        and only if it is a factory profile or a validated slot (P0-3)."""
        if not self.home_ready:
            self.home_ready = True
            pid = self.st["profile"]
            if not self.st["safe_mode"] and S.is_safe_to_apply(pid, self.st["slots"]) and pid != "balanced" \
                    and self.caps["governor"]:
                try:
                    self._apply_profile(pid)
                except StoneError as e:
                    log(f"stoned: could not apply {pid} at Home: {e.code} {e.message}")
            GLib.timeout_add_seconds(HOME_STABLE_S, self._boot_completed)
        return self.state()

    def _boot_completed(self):
        # Home stayed up: this boot is not a boot loop. The failure counter is only reset
        # by a clean shutdown (or RESTORE DEFAULTS), so two crashed tests in a row still
        # lead to Safe Mode even if Home came up in between.
        self.store.clear_flag(S.FLAG_BOOT)
        return False

    def SetProfile(self, pid):
        if self.run:
            raise StoneError(BUSY, "a stability test is running")
        if not S.is_profile_id(pid, self.st["slots"]):
            raise StoneError(INVALID_VALUE, f"unknown profile {pid!r}")
        k = S.slot_index(pid)
        if k is not None and self.st["slots"][k]["state"] == S.SLOT_FAILED:
            raise StoneError(NOT_VALIDATED, "this slot failed; test it again first")
        values = P.resolve(pid, self.st)
        current = self._actual()
        P.check_can_apply(values, self.caps, self.st, None if "error" in current else current,
                          self.backend.gpu_temp(), bool(self.run), self.thermal_reduced)
        provisional = not S.is_safe_to_apply(pid, self.st["slots"])
        if provisional:
            # An unvalidated slot is allowed only for this boot; the flag is written
            # *before* it reaches the governor so a crash is attributed to it.
            self.store.set_flag(S.FLAG_PROVISIONAL, {"profile": pid, "time": int(time.time())})
        else:
            self.store.clear_flag(S.FLAG_PROVISIONAL)
        self._apply_profile(pid)
        self.st["profile"] = pid
        if not provisional:
            self.st["last_known_good"] = pid
        self._save()
        self._changed()
        return self.state()

    def StartTest(self, slot, values, level):
        if self.run:
            raise StoneError(BUSY, "a stability test is already running")
        if level not in P.TEST_LEVELS:
            raise StoneError(INVALID_VALUE, "level must be quick, standard or extended")
        if not 0 <= slot < S.SLOT_COUNT:
            raise StoneError(INVALID_VALUE, "slot must be 0, 1 or 2")
        if not stability.available():
            raise StoneError(UNSUPPORTED, "stability workload not installed")
        vals = P.validate_values(values, self.caps)
        current = self._actual()
        P.check_can_apply(vals, self.caps, self.st, None if "error" in current else current,
                          self.backend.gpu_temp(), False, self.thermal_reduced)
        if self.st["safe_mode"]:
            raise StoneError(UNSUPPORTED, "no tests in Safe Mode")
        # LAST KNOWN GOOD first, then the trial flag, then the hardware.
        if S.is_safe_to_apply(self.st["profile"], self.st["slots"]):
            self.st["last_known_good"] = self.st["profile"]
        self._save()
        self.store.set_flag(S.FLAG_TRIAL, {"profile": f"c{slot + 1}", "values": vals, "level": level,
                                           "time": int(time.time())})
        try:
            self.backend.apply(vals)
            self.applied = "trial"
            self.run = stability.Run(slot, vals, level, self.backend)
            self.run.start()
            self.telemetry.hold("test", True)
        except (StoneError, OSError) as e:
            self._end_trial()
            if isinstance(e, StoneError):
                raise
            raise StoneError(UNSUPPORTED, f"could not start the workload: {e}") from None
        self.run_timer = GLib.timeout_add_seconds(1, self._tick)
        self._changed()
        return self.state()

    def MeasureBaseline(self, level):
        """BEFORE: the same load on what is applied now, changing nothing. Its result is
        what slot tests at the same level are compared against."""
        if self.run:
            raise StoneError(BUSY, "a stability test is already running")
        if level not in P.TEST_LEVELS:
            raise StoneError(INVALID_VALUE, "level must be quick, standard or extended")
        if not stability.available() or not self.caps["governor"]:
            raise StoneError(UNSUPPORTED, "stability workload or governor not available")
        if self.thermal_reduced:
            raise StoneError(P.THERMAL_LIMIT, "performance reduced by the thermal watch")
        pid = self._effective()
        try:
            self.run = stability.Run(None, P.resolve(pid, self.st), level, self.backend)
            self.run.baseline = pid
            self.run.start()
        except OSError as e:
            self.run = None
            raise StoneError(UNSUPPORTED, f"could not start the workload: {e}") from None
        self.telemetry.hold("test", True)
        self.run_timer = GLib.timeout_add_seconds(1, self._tick)
        self._changed()
        return self.state()

    def _baseline_for(self, level):
        for t in reversed(self.st["tests"]):
            if t.get("kind") == "baseline" and t.get("passed") and t.get("level") == level:
                return t
        return None

    def _tick(self):
        if not self.run:
            return False
        result = self.run.sample()
        if result is None:
            return True
        self._finish(result)
        return False

    def _end_trial(self):
        """Back to the last known good configuration and drop the trial flag."""
        try:
            self._apply_profile(self.st["last_known_good"])
            self.rollback_notice = None
        except StoneError as e:
            self.rollback_notice = e.code
        self.store.clear_flag(S.FLAG_TRIAL)

    def _finish(self, result):
        run, self.run = self.run, None
        self.telemetry.hold("test", False)
        if getattr(run, "baseline", None):
            result.update(kind="baseline", profile=run.baseline)
            self._record_test(result)       # nothing was applied, nothing to restore
            self.bus.emit_signal(None, PATH, IFACE, "TestFinished", GLib.Variant.new_tuple(to_variant(result)))
            self._changed()
            return
        result["kind"] = "slot"
        if result["passed"]:
            result["comparison"] = stability.compare(self._baseline_for(run.level), result)
        self._end_trial()
        # MODIFY -> REVIEW -> TEST -> TEMPORARY APPLY -> VALIDATE -> SAVE: a passed test is only
        # a result; nothing is stored until the user says SAVE.
        self.pending = {"slot": run.slot, "values": dict(run.values), "level": run.level} if result["passed"] else None
        self._record_test(result)
        self.bus.emit_signal(None, PATH, IFACE, "TestFinished", GLib.Variant.new_tuple(to_variant(result)))
        self._changed()

    def CancelTest(self):
        if not self.run:
            return self.state()
        self.run.stop_workload()
        baseline = getattr(self.run, "baseline", None)
        result = {"slot": self.run.slot, "level": self.run.level, "passed": False, "reason": "cancelled",
                  "time": int(self.run.wall0)}
        self.run = None
        self.telemetry.hold("test", False)
        if self.run_timer:
            GLib.source_remove(self.run_timer)
            self.run_timer = 0
        if not baseline:
            self._end_trial()          # a baseline never changed the hardware
        self._record_test(result)
        self._changed()
        return self.state()

    def SaveTestResult(self):
        if not self.pending:
            raise StoneError(INVALID_VALUE, "no passed test to save")
        k, vals, level = self.pending["slot"], self.pending["values"], self.pending["level"]
        self.pending = None
        prev = self.st["slots"][k]
        if prev and prev["state"] != S.SLOT_FAILED and prev["test_level"] in ("standard", "extended") \
                and {x: prev[x] for x in ("max", "min", "temp", "resp")} == vals and level == "quick":
            level = prev["test_level"]
        # Saved as TESTED: not applied, not used at boot (P0-3).
        self.st["slots"][k] = dict(vals, state=S.SLOT_TESTED, test_level=level)
        sid = f"c{k + 1}"
        if self.st["last_known_good"] == sid:
            self.st["last_known_good"] = S.DEFAULT_PROFILE
        if self.st["profile"] == sid:
            # The slot that was in use now holds new, unvalidated values.
            self.st["profile"] = self.st["last_known_good"]
            try:
                self._apply_profile(self.st["profile"])
            except StoneError as e:
                self.rollback_notice = e.code
        self._save()
        self._changed()
        return self.state()

    def DiscardTestResult(self):
        self.pending = None
        self._changed()
        return self.state()

    def SetSessionProfile(self, pid):
        """Quick Menu during a game: this session only, back to the global one when it ends."""
        if not self.game:
            raise StoneError(INVALID_VALUE, "no game running")
        if self.run:
            raise StoneError(BUSY, "a stability test is running")
        if not S.is_profile_id(pid, self.st["slots"]):
            raise StoneError(INVALID_VALUE, f"unknown profile {pid!r}")
        k = S.slot_index(pid)
        if k is not None and self.st["slots"][k]["state"] == S.SLOT_FAILED:
            raise StoneError(NOT_VALIDATED, "this slot failed; test it again first")
        values = P.resolve(pid, self.st)
        current = self._actual()
        P.check_can_apply(values, self.caps, self.st, None if "error" in current else current,
                          self.backend.gpu_temp(), False, self.thermal_reduced)
        prov = self.store.get_flag(S.FLAG_PROVISIONAL)
        if not S.is_safe_to_apply(pid, self.st["slots"]):
            self.store.set_flag(S.FLAG_PROVISIONAL, {"profile": pid, "appid": self.game["appid"], "time": int(time.time())})
        elif prov is not None and prov.get("appid") == self.game["appid"]:
            self.store.clear_flag(S.FLAG_PROVISIONAL)
        self._apply_profile(pid)
        self.game["profile"] = pid
        self._changed()
        return self.state()

    def RestoreDefaults(self):
        """BALANCED everywhere, per-game profiles removed, slots kept but not used."""
        if self.run:
            self.CancelTest()
        self.store.clear_flag(S.FLAG_PROVISIONAL)
        self.st["profile"] = self.st["last_known_good"] = S.DEFAULT_PROFILE
        self.st["per_game"] = {}
        self.pending = None
        # Restoring factory settings is a way out of Safe Mode, unless the boot asked for it.
        self._leave_safe_mode()
        if self.caps["governor"]:
            self.backend.restore_initial()
        self.applied = None
        self._save()
        self._changed()
        return self.state()

    def _leave_safe_mode(self):
        self.st["fail_count"] = 0
        self.st["safe_request"] = False
        kernel = recover_mod.kernel_requests_safe_mode()
        self.st["safe_reason"] = "kernel" if kernel else None
        self.st["safe_mode"] = kernel

    def EnterSafeMode(self):
        """Works at once and survives reboots until the user leaves it. Nothing above
        BALANCED, no per-game profiles, no tests; the governor back to its factory values.
        Slots and per-game assignments are kept, only unused."""
        if self.run:
            self.CancelTest()
        self.pending = None
        self.store.clear_flag(S.FLAG_PROVISIONAL)
        self.st["safe_request"] = True
        self.st["safe_mode"] = True
        if self.st["safe_reason"] != "kernel":
            self.st["safe_reason"] = "manual"
        if self.st["profile"] not in ("silent", "balanced"):
            self.st["profile"] = S.DEFAULT_PROFILE       # nothing above BALANCED
        if self.game:
            self.game["profile"] = self.st["profile"]
        if self.caps["governor"]:
            try:
                self.backend.restore_initial()
                self.applied = None
                if self.st["profile"] == "silent":
                    self._apply_profile("silent")
            except StoneError as e:
                log(f"stoned: Safe Mode could not reset the governor: {e.code}")
        self._save()
        self._changed()
        return self.state()

    def ExitSafeMode(self):
        if self.st["safe_reason"] == "kernel":
            raise StoneError(SAFE_MODE_ERR, "Safe Mode was requested on the kernel command line (stone.safe=1)")
        self._leave_safe_mode()
        self._save()
        self._changed()
        return self.state()

    def SetGameProfile(self, appid, pid):
        if not appid.isdigit():
            raise StoneError(INVALID_VALUE, "appid must be a Steam AppID")
        if pid in ("", "global"):
            self.st["per_game"].pop(appid, None)
        elif S.is_profile_id(pid, self.st["slots"]):
            self.st["per_game"][appid] = pid
        else:
            raise StoneError(INVALID_VALUE, f"unknown profile {pid!r}")
        self._save()
        self._changed()
        return self.state()

    def GameStarted(self, appid):
        if self.steam:
            raise StoneError(UNSUPPORTED, "game sessions are detected from Steam")
        return self._game_started(appid)

    def GameEnded(self, appid, normal):
        if self.steam:
            raise StoneError(UNSUPPORTED, "game sessions are detected from Steam")
        return self._game_ended(appid, normal)

    def _game_started(self, appid):
        if not appid.isdigit():
            raise StoneError(INVALID_VALUE, "appid must be a Steam AppID")
        pid = self.st["per_game"].get(appid) if not self.st["safe_mode"] else None
        self.game = {"appid": appid, "profile": pid or self.st["profile"]}
        if pid and pid != self.st["profile"] and not self.run:
            k = S.slot_index(pid)
            if k is None or self.st["slots"][k]["state"] != S.SLOT_FAILED:
                if not S.is_safe_to_apply(pid, self.st["slots"]):
                    self.store.set_flag(S.FLAG_PROVISIONAL, {"profile": pid, "appid": appid, "time": int(time.time())})
                try:
                    self._apply_profile(pid)
                except StoneError as e:
                    self.store.clear_flag(S.FLAG_PROVISIONAL)
                    log(f"stoned: per-game profile {pid} not applied: {e.code}")
                    self.game["profile"] = self.st["profile"]
        self._changed()
        return self.state()

    def _game_ended(self, appid, normal):
        """normal = a clean session: long enough and no GPU error in the kernel log."""
        prov = self.store.get_flag(S.FLAG_PROVISIONAL)
        same_game = self.game is not None and self.game["appid"] == appid
        if prov is not None and same_game:
            sid = prov.get("profile")
            k = S.slot_index(sid)
            s = self.st["slots"][k] if k is not None else None
            if s and normal and s["state"] == S.SLOT_TESTED and s["test_level"] in ("standard", "extended"):
                s["state"] = S.SLOT_VALIDATED          # STANDARD test + one clean game session
                if self.st["profile"] == sid:
                    self.st["last_known_good"] = sid
            # A per-game provisional slot ends with the game. A global one stays flagged
            # until it is validated.
            if prov.get("appid") == appid or (s and s["state"] == S.SLOT_VALIDATED):
                self.store.clear_flag(S.FLAG_PROVISIONAL)
        # Back to the global profile when the game goes away, whatever the reason.
        if same_game and self.game["profile"] != self.st["profile"]:
            try:
                self._apply_profile(self.st["profile"])
            except StoneError as e:
                log(f"stoned: global profile not restored: {e.code}")
        self.game = None
        self._save()
        self._changed()
        return self.state()


# ---- D-Bus plumbing ----------------------------------------------------------------------
class Polkit:
    def __init__(self, bus):
        self.bus = bus
        self.disabled = os.environ.get("STONE_POLKIT") == "0"   # test harness only
        if self.disabled:
            log("stoned: WARNING polkit checks disabled (STONE_POLKIT=0)")

    def allowed(self, sender, action):
        if self.disabled:
            return True
        try:
            r = self.bus.call_sync(
                "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
                "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
                GLib.Variant("((sa{sv})sa{ss}us)", (("system-bus-name", {"name": GLib.Variant("s", sender)}),
                                                     action, {}, 0, "")),
                GLib.VariantType("((bba{ss}))"), Gio.DBusCallFlags.NONE, 5000, None)
            return bool(r.unpack()[0][0])
        except GLib.Error as e:
            # no polkit, or polkit refused the question itself = no writes; said in the journal
            log(f"stoned: polkit check for {action} failed: {e.message}")
            return False


def serve(bus_type=None):
    bus_type = bus_type or (Gio.BusType.SESSION if os.environ.get("STONE_BUS") == "session" else Gio.BusType.SYSTEM)
    bus = Gio.bus_get_sync(bus_type, None)
    svc = Service(bus)
    pk = Polkit(bus)
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    in_types = {m.name: "(" + "".join(a.signature for a in m.in_args) + ")" for m in node.interfaces[0].methods}

    def on_call(conn, sender, path, iface, method, params, inv):
        try:
            if params.get_type_string() != in_types[method]:
                raise StoneError(INVALID_VALUE, "bad argument types")
            action = WRITE_METHODS.get(method)
            if action and not pk.allowed(sender, action):
                raise StoneError(PERMISSION_DENIED, "not authorized")
            args = params.unpack()
            if method in ("Subscribe", "Unsubscribe"):
                args = (sender,) + tuple(args)    # a subscription belongs to the caller's bus name
            out = getattr(svc, method)(*args)
            inv.return_value(GLib.Variant.new_tuple(to_variant(out)))
        except StoneError as e:
            inv.return_dbus_error(e.dbus_name, e.message or e.code)
        except Exception as e:   # never crash on a bad call; report and keep serving
            log(f"stoned: internal error in {method}: {e!r}")
            inv.return_dbus_error(PREFIX + "HardwareError", "internal error")

    bus.register_object(PATH, node.interfaces[0], on_call, None, None)

    # A client that disappears (UI closed or crashed) stops its telemetry subscription.
    def owner_changed(conn, sender, path, iface, signal, params):
        name, _old, new = params.unpack()
        if not new:
            svc.telemetry.name_gone(name)

    bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                         "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, owner_changed)
    loop = GLib.MainLoop()

    def lost(*_):
        log("stoned: lost the bus name")
        loop.quit()

    Gio.bus_own_name_on_connection(bus, NAME, Gio.BusNameOwnerFlags.NONE, None, lost)
    sd_notify("READY=1")
    GLib.timeout_add_seconds(5, lambda: (sd_notify("WATCHDOG=1"), True)[1])

    def stop(*_):
        if svc.run:
            svc.CancelTest()
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 15, stop)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 2, stop)
    loop.run()
