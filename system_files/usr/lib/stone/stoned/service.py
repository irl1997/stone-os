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
from . import calibration as C
from . import controller as CTL
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
CEILING_CHECK_S = 10           # how often the 40 CU ceiling is re-checked

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
    <method name="GetCalibration"><arg type="a{{sv}}" direction="out"/></method>
    <method name="StartCalibration"><arg type="s" name="level" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
    <method name="CancelCalibration"><arg type="a{{sv}}" direction="out"/></method>
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
                 "EnterSafeMode": ACT_CHANGE, "ExitSafeMode": ACT_CHANGE, "MeasureBaseline": ACT_TUNE,
                 "StartCalibration": ACT_TUNE, "CancelCalibration": ACT_CHANGE}


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
        self.cal = None                # running calibration {"level","steps","plan","run","done"}
        self.trim = CTL.TrimState()    # dynamic controller during games
        self._refresh_range()
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
        self._recover_calibration_crash()
        self._enforce_ceiling()
        GLib.timeout_add_seconds(CEILING_CHECK_S, lambda: (self._enforce_ceiling(), True)[1])
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

    def _enforce_ceiling(self):
        """40 CU in use: the GPU stays at or below its ceiling even if the governor restarted
        from its factory file (which allows more)."""
        if not self.caps["governor"]:
            return
        try:
            if self.backend.enforce_ceiling():
                log(f"stoned: 40 CU in use, GPU limited to {self.backend.ceiling()} MHz")
        except StoneError as e:
            log(f"stoned: could not apply the 40 CU ceiling: {e.code} {e.message}")

    def _refresh_range(self):
        """Performance range of THIS machine now (calibration.py); the backend enforces its
        validated maximum on everything that reaches the governor."""
        self.config = C.configuration(self.backend, self.caps)
        self.machine = C.machine_id(self.backend)
        self.range = C.performance_range(self.caps, self.st.get("calibration"), self.config, self.machine)
        if self.caps.get("governor"):
            self.backend.set_ceiling(self.range["validated_max"])

    def _recover_calibration_crash(self):
        """stoned restarted during a calibration step (not a reboot: recover.py handles that)."""
        flag = self.store.get_flag(S.FLAG_CALIBRATION)
        if flag is None:
            return
        log("stoned: calibration interrupted by a service restart")
        self._store_calibration(C.record(flag.get("machine", self.machine), flag.get("config", self.config),
                                         flag.get("level", "quick"), flag.get("steps", []), flag.get("step"),
                                         "interrupted", (self.range["hardware"] or [0, 0])[1], flag.get("workload")))
        self.store.clear_flag(S.FLAG_CALIBRATION)
        try:
            self._apply_profile(self.st["last_known_good"])
        except StoneError as e:
            self.rollback_notice = e.code

    def _store_calibration(self, rec):
        self.st["calibration"] = rec
        self._save()
        self._refresh_range()

    # ---- helpers ----------------------------------------------------------------------
    def _save(self):
        self.store.save(self.st)
        self.st = self.store.load()

    def _apply_profile(self, pid):
        values = P.resolve(pid, self.st, self.range)
        if self.thermal_reduced and values["max"] > P.factory("silent", self.range)["max"]:
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
            "range": self.range,
            "profiles": {pid: P.factory(pid, self.range) for pid in S.FACTORY_PROFILES},
            "calibration": self._cal_view() if self.cal is not None else {"running": None, "last": (self.st.get("calibration") or {}).get("result")},
            "dynamic": {"trim_mhz": self.trim.trim},
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
        c["calibration"] = c["governor"] and (C.workload(60)[0] is not None or stability.available())
        c["range"] = self.range
        c["hardware_range"] = list(c.get("hardware_range") or c["gpu_range"])
        cu = dict(c.get("cu40") or {})
        if cu.get("active"):
            cu["ceiling_mhz"] = self.range["validated_max"]          # THIS machine's validated maximum
        c["cu40"] = cu
        c.pop("initial_range", None)
        return c

    def GetState(self):
        return self.state()

    def GetMetrics(self):
        """Live readings + what the future STONE GAME PROFILE needs from STONE PERFORMANCE."""
        m = dict(self.telemetry.sample())
        a = self._actual()
        limit = a.get("temp") if isinstance(a, dict) else None
        t = m.get("gpu_temp_c")
        m.update(cu_count=self.range.get("cu"), validated_max_mhz=self.range.get("validated_max"),
                 baseline_mhz=self.range.get("baseline"), ceiling_mhz=a.get("max") if isinstance(a, dict) else None,
                 thermal_headroom_c=round(limit - t, 1) if (limit and t is not None) else None,
                 governor="ok" if self.caps.get("governor") and "error" not in a else "unavailable",
                 profile=self._effective(), dynamic_trim_mhz=self.trim.trim)
        return m

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
        self._dynamic(gpu, (last or {}).get("gpu_busy_pct") if fresh else None)
        if self.thermal.interval_s() != before:
            self._thermal_timer = GLib.timeout_add(int(self.thermal.interval_s() * 1000), self._thermal_tick)
            return False
        return True

    def _dynamic(self, gpu_temp, busy):
        """Dynamic controller (controller.py) during a game: the ceiling moves inside
        [baseline, profile maximum] for thermal headroom; the governor does the rest."""
        if not (self.game and self.caps.get("governor") and not self.run and self.cal is None
                and not self.thermal_reduced and not self.st["safe_mode"]):
            return
        try:
            values = P.resolve(self.game["profile"], self.st, self.range)
        except StoneError:
            return
        ceiling, why = CTL.decide(self.trim, values["max"], self.range["baseline"], gpu_temp, values["temp"], busy)
        if not why:
            return
        try:
            self.backend.apply(dict(values, max=ceiling))
            log(f"stoned: dynamic ceiling {ceiling} MHz ({why}, GPU {gpu_temp} C)")
            self._changed()
        except StoneError as e:
            log(f"stoned: dynamic ceiling not applied: {e.code}")

    def _effective(self):
        return (self.game or {}).get("profile") or self.st["profile"]

    def _thermal_reduce(self, reason):
        """Lower only. SILENT is applied temporarily; the user's choice is kept, except a
        CUSTOM slot in use, which loses its validation (it ran too hot)."""
        if self.cal is not None:
            self._cal_stop("thermal")
        elif self.run:
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
            self.backend.apply(P.factory("silent", self.range))
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
        values = P.resolve(pid, self.st, self.range)
        current = self._actual()
        P.check_can_apply(values, self.caps, self.st, None if "error" in current else current,
                          self.backend.gpu_temp(), bool(self.run), self.thermal_reduced, self.range)
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
                          self.backend.gpu_temp(), False, self.thermal_reduced, self.range)
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
            self.run = stability.Run(None, P.resolve(pid, self.st, self.range), level, self.backend)
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
        if self.cal is not None:
            return self._cal_step_done(result)
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
        if self.cal is not None:
            return self.CancelCalibration()
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

    # ---- STONE CALIBRATION ------------------------------------------------------------------
    def GetCalibration(self):
        return self._cal_view()

    def _cal_view(self):
        rec = self.st.get("calibration") or {}
        run = None
        if self.cal is not None:
            run = {"level": self.cal["level"], "step_mhz": self.cal["plan"][self.cal["i"]] if self.cal["i"] < len(self.cal["plan"]) else None,
                   "step": self.cal["i"] + 1, "of": len(self.cal["plan"]), "passed": [x["mhz"] for x in self.cal["steps"] if x["passed"]],
                   "seconds": round(self.run.clock() - self.run.t0) if self.run else 0,
                   "step_seconds": self.run.duration if self.run else None, "max_temp_c": self.run.max_temp if self.run else None,
                   "workload": self.cal["workload"]}
        return {"range": self.range, "running": run,
                "last": {k: rec.get(k) for k in ("time", "level", "validated_max", "result", "failed_at", "stopped",
                                                  "max_temp_c", "workload", "steps")} if rec else None}

    def StartCalibration(self, level):
        """Progressive search of THIS machine's highest stable GPU ceiling (calibration.py).
        Started by the person only; never at boot, never by a game."""
        if level not in C.STEP_SECONDS:
            raise StoneError(INVALID_VALUE, "level must be quick or standard")
        if self.run or self.cal is not None:
            raise StoneError(BUSY, "a test is already running")
        if not self.caps.get("governor"):
            raise StoneError(UNSUPPORTED, "GPU governor not available")
        if self.st["safe_mode"]:
            raise StoneError(UNSUPPORTED, "no calibration in Safe Mode")
        if self.game:
            raise StoneError(BUSY, "close the game first")
        if self.thermal_reduced:
            raise StoneError(P.THERMAL_LIMIT, "performance reduced by the thermal watch")
        t = self.backend.gpu_temp()
        if t is not None and t >= C.TEMP_LIMIT_C - 15:
            raise StoneError(P.THERMAL_LIMIT, f"GPU at {t} C: let it cool down first")
        cmd, wname = C.workload(C.STEP_SECONDS[level])
        if cmd is None and not stability.available():
            raise StoneError(UNSUPPORTED, "no calibration workload installed")
        hi = self.range["hardware"][1]
        plan = C.steps(self.range["baseline"], hi)
        if not plan:
            raise StoneError(UNSUPPORTED, "no frequency range to calibrate")
        # LAST KNOWN GOOD before anything reaches the hardware.
        if S.is_safe_to_apply(self.st["profile"], self.st["slots"]):
            self.st["last_known_good"] = self.st["profile"]
        self._save()
        self.cal = {"level": level, "plan": plan, "i": 0, "steps": [], "cmd": cmd, "workload": wname}
        log(f"stoned: calibration ({level}, {wname}) {plan[0]}..{plan[-1]} MHz")
        self.telemetry.hold("test", True)
        self._cal_start_step()
        self._changed()
        return self.state()

    def _cal_values(self, mhz):
        lo = max(P.STONE_MIN[0], (self.range["hardware"] or [1000])[0])
        return {"max": mhz, "min": min(lo, mhz), "temp": C.TEMP_LIMIT_C, "resp": 1}

    def _cal_start_step(self):
        mhz = self.cal["plan"][self.cal["i"]]
        # The flag goes first: a hang or power cut is attributed to this step at the next boot.
        self.store.set_flag(S.FLAG_CALIBRATION, {"step": mhz, "steps": self.cal["steps"], "level": self.cal["level"],
                                                 "config": self.config, "machine": self.machine,
                                                 "workload": self.cal["workload"], "time": int(time.time())})
        vals = self._cal_values(mhz)
        try:
            self.backend.set_ceiling(mhz)                  # this step only
            self.backend.apply(vals)
            self.applied = "calibration"
            self.run = stability.Run(None, vals, "standard", self.backend, cmd=self.cal["cmd"],
                                     duration=C.STEP_SECONDS[self.cal["level"]])
            self.run.start()
        except (StoneError, OSError) as e:
            log(f"stoned: calibration step {mhz} MHz could not start: {e!r}")
            self.run = None
            return self._cal_end("apply_failed" if isinstance(e, StoneError) else "workload_failed")
        self.run_timer = GLib.timeout_add_seconds(1, self._tick)

    def _cal_step_done(self, result):
        self.run = None
        mhz = self.cal["plan"][self.cal["i"]]
        step = {"mhz": mhz, "passed": bool(result["passed"]), "reason": result.get("reason"),
                "max_temp_c": result.get("max_temp_c"), "median_clock_mhz": result.get("median_clock_mhz"),
                "avg_power_w": result.get("avg_power_w"), "seconds": result.get("seconds")}
        self.cal["steps"].append(step)
        log(f"stoned: calibration {mhz} MHz {'passed' if step['passed'] else 'FAILED: ' + str(step['reason'])}"
            f" (max {step['max_temp_c']} C, median {step['median_clock_mhz']} MHz)")
        if not step["passed"]:
            return self._cal_end("thermal" if step["reason"] in ("thermal", "cpu_thermal") else None, failed_at=mhz)
        self.cal["i"] += 1
        if self.cal["i"] >= len(self.cal["plan"]):
            return self._cal_end(None)
        self._cal_start_step()
        self._changed()

    def _cal_stop(self, why):
        """Thermal watch or cancel: stop the running step now."""
        if self.cal is None:
            return
        if self.run:
            self.run.stop_workload()
            self.run = None
        if self.run_timer:
            GLib.source_remove(self.run_timer)
            self.run_timer = 0
        self._cal_end(why)

    def _cal_end(self, stopped, failed_at=None):
        cal, self.cal = self.cal, None
        self.run = None
        self.telemetry.hold("test", False)
        self.backend.set_ceiling(self.range["validated_max"])
        if stopped == "cancelled" and not cal["steps"]:
            self.store.clear_flag(S.FLAG_CALIBRATION)
        elif stopped == "cancelled":
            log("stoned: calibration cancelled; the previous result is kept")
            self.store.clear_flag(S.FLAG_CALIBRATION)
        else:
            rec = C.record(self.machine, self.config, cal["level"], cal["steps"], failed_at, stopped,
                           self.range["hardware"][1], cal["workload"])
            self.store.clear_flag(S.FLAG_CALIBRATION)
            self._store_calibration(rec)
            log(f"stoned: calibration result {rec['result']}: validated maximum {rec['validated_max']} MHz")
            self.bus.emit_signal(None, PATH, IFACE, "TestFinished",
                                 GLib.Variant.new_tuple(to_variant(dict(kind="calibration", passed=rec["validated_max"] is not None,
                                                                        **{k: rec[k] for k in ("validated_max", "result", "failed_at", "stopped")}))))
        try:
            self._apply_profile(self.st["last_known_good"])      # back to the saved, now re-resolved profile
        except StoneError as e:
            self.rollback_notice = e.code
        self._changed()

    def CancelCalibration(self):
        if self.cal is None:
            return self.state()
        self._cal_stop("cancelled")
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
        values = P.resolve(pid, self.st, self.range)
        current = self._actual()
        P.check_can_apply(values, self.caps, self.st, None if "error" in current else current,
                          self.backend.gpu_temp(), False, self.thermal_reduced, self.range)
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
        self.trim.reset()
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
        self.trim.reset()
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
