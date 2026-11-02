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

A load that did not use the GPU proves nothing either way. How busy the GPU was comes from, in
order: the GPU activity sensor (gpu_metrics; absent on the BC-250 without the governor's
fix-metrics), stone-cu-verify's own account of its GPU time (pass=... seconds=... in
workload.log), and only then the temperature rise. Below LIGHT_BUSY_PCT the step is
"load_too_light", never an unstable GPU.

The BC-250's pp_dpm_sclk does not show the real clock (board, rc14: "0: 1000Mhz / 1: 5Mhz * /
2: 2000Mhz" under full load), so a clock below CLOCK_CREDIBLE_MHZ is "not readable" and the
sustained-clock rule is skipped rather than failing the step. What stands in for it is a real
measurement: stone-cu-verify's time per pass (fixed work), which shrinks as the clock rises;
the calibration records it per step and compares steps.
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
LIGHT_BUSY_PCT = 50      # median GPU activity below this: the load did not use the GPU
LIGHT_RISE_C = 8         # without any other evidence: warmed less than this
CLOCK_CREDIBLE_MHZ = 200 # a "current clock" below this under load is not a reading (board, rc14:
                         # pp_dpm_sclk shows "1: 5Mhz *" while the GPU computes at full speed)
CPU_STOP_C = 95          # calibration steps stop here (Zen 2 TJmax is 100 C)


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
    def __init__(self, slot, values, level, backend, clock=time.monotonic, cmd=None, duration=None):
        self.slot, self.values, self.level = slot, values, level
        self.backend, self.clock = backend, clock
        self.cmd = cmd                      # calibration: stone-cu-verify instead of the default load
        self.duration = int(os.environ.get("STONE_TEST_SECONDS") or duration or P.TEST_LEVELS[level])
        self.t0 = self.clock()
        self.wall0 = time.time()
        self.proc = None
        self.samples = []
        self.max_temp = None
        self.reason = None

    def log_path(self):
        from .state import state_dir
        return os.path.join(state_dir(), "workload.log")

    def start(self):
        env = dict(os.environ)
        cache = os.path.join(os.path.dirname(self.log_path()), "cache")
        try:
            os.makedirs(cache, exist_ok=True)
            env.update(HOME=os.path.dirname(self.log_path()), XDG_CACHE_HOME=cache)
        except OSError:
            pass
        try:
            out = open(self.log_path(), "w")
        except OSError:
            out = subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(self.cmd or workload_cmd(), stdin=subprocess.DEVNULL, stdout=out,
                                         stderr=subprocess.STDOUT, start_new_session=True, env=env)
        finally:
            if out is not subprocess.DEVNULL:
                out.close()

    def workload_tail(self, lines=6):
        try:
            with open(self.log_path(), errors="replace") as f:
                return [x.strip()[:200] for x in f.read()[-4000:].splitlines() if x.strip()][-lines:]
        except OSError:
            return []

    def sample(self):
        """Called once per second. Returns None while running, or a finished result."""
        m = self.backend.metrics()
        t = m.get("gpu_temp_c")
        if t is not None:
            self.max_temp = t if self.max_temp is None else max(self.max_temp, t)
        self.samples.append({"t": round(self.clock() - self.t0, 1), "temp": t, "busy": m.get("gpu_busy_pct"),
                             "clock": m.get("gpu_clock_mhz"), "power": m.get("gpu_power_w")})
        if t is not None and t > self.values["temp"] + TEMP_MARGIN:
            return self.finish(False, "thermal")
        c = m.get("cpu_temp_c")
        if self.cmd and c is not None and c >= CPU_STOP_C:
            # calibration: a 40 CU GPU at high clocks also heats the CPU next to it (one die)
            return self.finish(False, "cpu_thermal")
        if self.proc and self.proc.poll() is not None and self.proc.returncode != 0:
            # stone-cu-verify exits 2 when the GPU computed wrong results
            return self.finish(False, "wrong_results" if self.cmd and self.proc.returncode == 2 else "workload_failed")
        if self.clock() - self.t0 >= self.duration:
            return self.finish(True, None)
        return None

    def stop_workload(self):
        """The whole process group, also when its leader already ended (a calibration load
        started next to stone-cu-verify must not outlive it)."""
        if not self.proc:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def finish(self, ok, reason):
        self.stop_workload()
        errs = kernel_errors_since(self.wall0)
        if ok and errs is None:
            ok, reason = False, "kernel_log_unavailable"
        elif ok and errs:
            ok, reason = False, "gpu_errors"
        raw = [s["clock"] for s in self.samples if s["clock"] is not None]
        credible = bool(raw) and sum(1 for c in raw if c >= CLOCK_CREDIBLE_MHZ) * 2 > len(raw)
        clocks = raw if credible else []
        # Throttling: at the thermal limit and clearly below the requested ceiling.
        timed = [s for s in self.samples if s["clock"] is not None and s["temp"] is not None] if credible else []
        thr = [s for s in timed if s["temp"] >= self.values["temp"] - 1 and s["clock"] < 0.95 * self.values["max"]]
        throttle_pct = round(100 * len(thr) / len(timed), 1) if timed else None
        powers = [s["power"] for s in self.samples if s["power"] is not None]
        sustained = sorted(clocks)[len(clocks) // 2] if clocks else None   # median
        busy = sorted(s["busy"] for s in self.samples if s.get("busy") is not None)
        busy_median = busy[len(busy) // 2] if busy else None
        temps = [s["temp"] for s in self.samples if s["temp"] is not None]
        rise = (max(temps) - temps[0]) if temps else None
        verify = self.verify_stats()
        if busy_median is not None:
            light = busy_median < LIGHT_BUSY_PCT
        elif verify["gpu_share_pct"] is not None:
            light = verify["gpu_share_pct"] < LIGHT_BUSY_PCT
        else:
            light = rise is not None and rise < LIGHT_RISE_C
        if ok and self.level != "quick":
            hot = self.max_temp is not None and self.max_temp >= self.values["temp"] - 1
            if credible:
                if not hot and sustained < 0.9 * self.values["max"]:
                    ok, reason = False, "load_too_light" if light else "clock_not_sustained"
            elif light or (busy_median is None and verify["gpu_share_pct"] is None and rise is None):
                ok, reason = False, "load_too_light"     # clock not readable and nothing shows the GPU worked
        return {"slot": self.slot, "level": self.level, "passed": ok, "reason": reason,
                "seconds": round(self.clock() - self.t0), "max_temp_c": self.max_temp,
                "median_clock_mhz": sustained, "throttle_pct": throttle_pct,
                "avg_power_w": round(sum(powers) / len(powers), 1) if powers else None,
                "values": dict(self.values), "time": int(self.wall0),
                "gpu_errors": (errs or [])[:5], "busy_median_pct": busy_median,
                "temp_rise_c": round(rise, 1) if rise is not None else None,
                "clock_readable": credible, "clock_raw_median_mhz": sorted(raw)[len(raw) // 2] if raw else None,
                "verify_pass_s": verify["pass_s"], "verify_gpu_share_pct": verify["gpu_share_pct"],
                "workload_output": self.workload_tail() if not ok else []}

    def verify_stats(self):
        """stone-cu-verify's own report of this run: median GPU seconds per pass (fixed work, so
        it falls as the clock rises) and its GPU time as a share of the run."""
        out = {"pass_s": None, "gpu_share_pct": None}
        try:
            with open(self.log_path(), errors="replace") as f:
                text = f.read(4 * 2**20)
        except OSError:
            return out
        secs = []
        for line in text.splitlines():
            if line.startswith("pass="):
                for part in line.split():
                    if part.startswith("seconds="):
                        try:
                            secs.append(float(part[8:]))
                        except ValueError:
                            pass
        if not secs:
            return out
        elapsed = max(self.clock() - self.t0, 1e-6)
        out["pass_s"] = round(sorted(secs)[len(secs) // 2], 4)
        out["gpu_share_pct"] = round(min(100.0, 100.0 * sum(secs) / elapsed), 1)
        return out
