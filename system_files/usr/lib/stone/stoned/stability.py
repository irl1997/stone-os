"""STONE stability test runner.

A passed test reduces uncertainty; it never proves stability. The UI says
"Sin errores en la prueba", never "ESTABLE".

The workload runs as the `stoned` user on the GPU render node, with a fixed argument
list read from a root-owned file - never from IPC. Default: vkmark (installed by the
image) with its headless window system. [VERIFICAR en placa: vkmark --winsys headless]

Pass criteria, all of them:
  * the workload is still running (or ended normally) when the time is up,
  * no amdgpu error in the kernel log since the test started,
  * GPU temperature never above the requested limit + 5 C (the governor should hold it),
  * sustained clock >= 90 % of the requested max while not throttling (STANDARD and up).
"""

import json
import os
import shutil
import signal
import subprocess
import time

from . import profiles as P

CONF = os.environ.get("STONE_STABILITY_CONF", "/usr/lib/stone/stability.json")
DEFAULT_CMD = ["vkmark", "--winsys", "headless", "--run-forever"]
AMDGPU_ERRORS = ("ring gfx timeout", "gpu reset", "amdgpu: *error*", "page fault", "gpu hang")
TEMP_MARGIN = 5


def workload_cmd():
    try:
        with open(CONF) as f:
            cmd = json.load(f).get("command")
        if isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
            return cmd
    except (OSError, ValueError, AttributeError):
        pass
    return list(DEFAULT_CMD)


def available():
    return shutil.which(workload_cmd()[0]) is not None


def kernel_errors_since(t0):
    """amdgpu errors in the kernel log since t0. Needs the systemd-journal group."""
    try:
        out = subprocess.run(["journalctl", "-k", "--no-pager", "-o", "cat", f"--since=@{int(t0)}"],
                             capture_output=True, text=True, timeout=10).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return None   # unknown: the test cannot pass without this check
    return [line for line in out.splitlines() if "amdgpu" in line and any(e in line for e in AMDGPU_ERRORS)]


NO_GAIN_PCT = 2.0          # less than this over the baseline = more clock does not help
THROTTLE_WORSE_PTS = 10.0  # this many more percentage points of throttling = worse


def compare(before, after):
    """BEFORE / AFTER of two passed runs at the same level. Measures sustained GPU clock,
    temperature, power and time throttled - not FPS: FPS only exist inside a game
    (MangoHud) and stoned cannot read them."""
    if not before or not after or not before.get("median_clock_mhz") or after.get("median_clock_mhz") is None:
        return {"verdict": "insufficient"}
    gain = round(100.0 * (after["median_clock_mhz"] - before["median_clock_mhz"]) / before["median_clock_mhz"], 1)
    d = lambda k: (round(after[k] - before[k], 1) if after.get(k) is not None and before.get(k) is not None else None)
    tb, ta = before.get("throttle_pct") or 0.0, after.get("throttle_pct") or 0.0
    if ta - tb >= THROTTLE_WORSE_PTS or gain < 0:
        verdict = "worse"            # thermal or power limits eat the extra clock
    elif gain < NO_GAIN_PCT:
        verdict = "no_gain"
    else:
        verdict = "better"
    return {"verdict": verdict, "clock_gain_pct": gain, "temp_delta_c": d("max_temp_c"),
            "power_delta_w": d("avg_power_w"), "throttle_delta_pts": round(ta - tb, 1),
            "baseline_profile": before.get("profile"), "level": after.get("level")}


class Run:
    def __init__(self, slot, values, level, backend, clock=time.monotonic):
        self.slot, self.values, self.level = slot, values, level
        self.backend, self.clock = backend, clock
        self.duration = int(os.environ.get("STONE_TEST_SECONDS", P.TEST_LEVELS[level]))
        self.t0 = self.clock()
        self.wall0 = time.time()
        self.proc = None
        self.samples = []
        self.max_temp = None
        self.reason = None

    def start(self):
        self.proc = subprocess.Popen(workload_cmd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)

    def sample(self):
        """Called once per second. Returns None while running, or a finished result."""
        m = self.backend.metrics()
        t = m.get("gpu_temp_c")
        if t is not None:
            self.max_temp = t if self.max_temp is None else max(self.max_temp, t)
        self.samples.append({"t": round(self.clock() - self.t0, 1), "temp": t,
                             "clock": m.get("gpu_clock_mhz"), "power": m.get("gpu_power_w")})
        if t is not None and t > self.values["temp"] + TEMP_MARGIN:
            return self.finish(False, "thermal")
        if self.proc and self.proc.poll() is not None and self.proc.returncode != 0:
            return self.finish(False, "workload_failed")
        if self.clock() - self.t0 >= self.duration:
            return self.finish(True, None)
        return None

    def stop_workload(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def finish(self, ok, reason):
        self.stop_workload()
        errs = kernel_errors_since(self.wall0)
        if ok and errs is None:
            ok, reason = False, "kernel_log_unavailable"
        elif ok and errs:
            ok, reason = False, "gpu_errors"
        clocks = [s["clock"] for s in self.samples if s["clock"] is not None]
        # Throttling: at the thermal limit and clearly below the requested ceiling.
        timed = [s for s in self.samples if s["clock"] is not None and s["temp"] is not None]
        thr = [s for s in timed if s["temp"] >= self.values["temp"] - 1 and s["clock"] < 0.95 * self.values["max"]]
        throttle_pct = round(100 * len(thr) / len(timed), 1) if timed else None
        powers = [s["power"] for s in self.samples if s["power"] is not None]
        sustained = sorted(clocks)[len(clocks) // 2] if clocks else None   # median
        if ok and self.level != "quick":
            hot = self.max_temp is not None and self.max_temp >= self.values["temp"] - 1
            if sustained is None or (not hot and sustained < 0.9 * self.values["max"]):
                ok, reason = False, "clock_not_sustained"
        return {"slot": self.slot, "level": self.level, "passed": ok, "reason": reason,
                "seconds": round(self.clock() - self.t0), "max_temp_c": self.max_temp,
                "median_clock_mhz": sustained, "throttle_pct": throttle_pct,
                "avg_power_w": round(sum(powers) / len(powers), 1) if powers else None,
                "values": dict(self.values), "time": int(self.wall0),
                "gpu_errors": (errs or [])[:5]}
