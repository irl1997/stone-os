"""Background thermal watch (audit section 6). Works ON TOP of the existing protections:
the board's own thermal cut-off, firmware/amdgpu throttling and the governor's threshold
all stay active. STONE only ever lowers performance here; it never raises a limit and
never disables a protection.

  Warning    GPU >= 85 C for 30 s                      -> notice
  Reduce     GPU >= 88 C for 30 s, or fan failure      -> SILENT applied temporarily
  Restore    Reduce while a CUSTOM slot was in use      -> BALANCED, slot no longer validated
  Recover    GPU <= 75 C for 60 s after Reduce         -> the profile in use is applied again
  Critical   GPU >= 92 C with SILENT already applied    -> critical notice. STONE does not shut
             the machine down: that is the board's thermal trip, which it never overrides.

Background read every 5 s (one file), every 1 s while warning or reduced.
Thresholds follow community figures (85 C recommended, 90 C max) [AJUSTAR en placa].
"""

import os
import time

WARN_C, REDUCE_C, RECOVER_C, CRITICAL_C = 85, 88, 75, 92
WARN_S, REDUCE_S, RECOVER_S = 30, 30, 60
FAN_FAIL_MIN_C = 70
BASE_INTERVAL_S, ALERT_INTERVAL_S = 5, 1


def scale():
    try:
        return float(os.environ.get("STONE_THERMAL_SCALE", "1"))   # test harness only
    except ValueError:
        return 1.0


class ThermalWatch:
    def __init__(self, on_reduce, on_recover, notify, clock=time.monotonic):
        self.on_reduce, self.on_recover, self.notify = on_reduce, on_recover, notify
        self.clock = clock
        self.k = scale()
        self.level = "normal"
        self.reason = None
        self.hot_since = None       # >= WARN_C
        self.vhot_since = None      # >= REDUCE_C
        self.cool_since = None      # <= RECOVER_C
        self.fan_seen = False
        self.fan_failed = False
        self.critical_sent = False

    def interval_s(self):
        return (ALERT_INTERVAL_S if self.level != "normal" else BASE_INTERVAL_S) * self.k

    def view(self):
        return {"level": self.level, "reason": self.reason}

    def update(self, gpu_c, fan_rpm):
        now = self.clock()
        if gpu_c is None:
            return self.level                 # unknown is not "cool": nothing changes
        self.hot_since = (self.hot_since or now) if gpu_c >= WARN_C else None
        self.vhot_since = (self.vhot_since or now) if gpu_c >= REDUCE_C else None
        self.cool_since = (self.cool_since or now) if gpu_c <= RECOVER_C else None

        if fan_rpm is not None:
            if fan_rpm > 0:
                self.fan_seen, self.fan_failed = True, False
            elif self.fan_seen and gpu_c >= FAN_FAIL_MIN_C:
                self.fan_failed = True        # was spinning, now 0, and getting hot; stays until it spins

        if self.level == "normal" and self.hot_since is not None and now - self.hot_since >= WARN_S * self.k:
            self.level, self.reason = "warning", "temperature"
            self.notify({"level": "warning", "reason": "temperature", "gpu_temp_c": gpu_c})
        if self.level != "reduced":
            if self.fan_failed or (self.vhot_since is not None and now - self.vhot_since >= REDUCE_S * self.k):
                self.level, self.reason = "reduced", ("fan" if self.fan_failed else "temperature")
                self.critical_sent = False
                self.on_reduce(self.reason)
                self.notify({"level": "reduced", "reason": self.reason, "gpu_temp_c": gpu_c})
            elif self.level == "warning" and self.hot_since is None:
                self.level, self.reason = "normal", None
                self.notify({"level": "normal", "gpu_temp_c": gpu_c})
        else:
            if gpu_c >= CRITICAL_C and not self.critical_sent:
                self.critical_sent = True
                self.notify({"level": "critical", "reason": self.reason, "gpu_temp_c": gpu_c})
            if self.cool_since is not None and now - self.cool_since >= RECOVER_S * self.k and not self.fan_failed:
                self.level, self.reason = "normal", None
                self.on_recover()
                self.notify({"level": "normal", "gpu_temp_c": gpu_c})
        return self.level
