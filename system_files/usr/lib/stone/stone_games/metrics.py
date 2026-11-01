"""Frame metrics from MangoHud's CSV log: only what was really measured, nothing estimated.

MangoHud is turned on for a measurement launch only, without overlay:
  MANGOHUD=1 MANGOHUD_CONFIG=no_display,log_interval=0,autostart_log=<delay s>,log_duration=<s>,output_folder=<dir>
[VERIFICAR en placa: the log appears in <dir> after the game (MangoHud version of the image)]

The CSV: a system-info header (os,cpu,gpu,...) and its row, then a column header starting with
"fps,frametime" and one row per frame (log_interval=0). Columns used when present:
  fps, frametime (ms), gpu_core_clock (MHz), gpu_temp (C), cpu_temp (C), gpu_power (W), elapsed

Definitions (STONE_OPTIMIZED.md):
  avg_fps          frames / sum of frametimes (not the mean of the fps column)
  low1_fps         1% low: 1000 / mean of the slowest 1% of frametimes
  frametime_*      mean, standard deviation, p50, p99 (ms); variance = stdev^2
  frametime_cv     stdev / mean (frame pacing; 0 = perfectly even)
  stutters         frames slower than 2.5x the median
  gpu_clock_avg, gpu_temp_max, cpu_temp_max, power_avg (as MangoHud read them; the BC-250's power
                   reading is NOT validated: power_reliable is False until a board shows otherwise)
The first WARMUP_S seconds of the log are dropped (loading, shader compilation).
"""

import csv
import glob
import math
import os

WARMUP_S = 5.0
MIN_FRAMES = 600
MIN_SECONDS = 20.0


def latest_log(folder, exclude=()):
    """The newest CSV that was not there before the measurement started."""
    files = [f for f in glob.glob(os.path.join(folder, "*.csv")) if os.path.basename(f) not in set(exclude)]
    files = [f for f in files if not f.endswith("_summary.csv")]
    return max(files, key=os.path.getmtime) if files else None


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def read_rows(path, limit=4 * 2**20):
    with open(path, newline="", errors="replace") as f:
        lines = f.read(64 * 2**20).splitlines()
    head = next((i for i, l in enumerate(lines) if l.lower().startswith("fps,") or l.lower().startswith("fps;")), None)
    if head is None:
        return [], []
    rows = list(csv.reader(lines[head:head + 1 + limit]))
    cols = [c.strip().lower() for c in rows[0]]
    return cols, rows[1:]


def summarize(path):
    """The metrics dict, or {"error": ...} when the log cannot support a measurement."""
    try:
        cols, rows = read_rows(path)
    except OSError as e:
        return {"error": f"log unreadable ({e.__class__.__name__})"}
    if "frametime" not in cols:
        return {"error": "no frametime column in the log"}
    idx = {c: cols.index(c) for c in cols}

    def col(r, name):
        i = idx.get(name)
        return _num(r[i]) if i is not None and i < len(r) else None

    frames, t = [], 0.0
    for r in rows:
        ft = col(r, "frametime")
        if ft is None or ft <= 0 or ft > 5000:
            continue
        t += ft / 1000.0
        if t < WARMUP_S:
            continue
        frames.append((ft, col(r, "gpu_core_clock"), col(r, "gpu_temp"), col(r, "cpu_temp"), col(r, "gpu_power")))
    if len(frames) < MIN_FRAMES or sum(f[0] for f in frames) / 1000.0 < MIN_SECONDS:
        return {"error": f"too little data ({len(frames)} frames after warm-up)", "frames": len(frames)}
    ft = [f[0] for f in frames]
    n, total = len(ft), sum(ft)
    mean = total / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in ft) / n)
    s = sorted(ft)
    worst = s[-max(1, n // 100):]
    p50 = _pct(s, 0.50)

    def avg(i):
        v = [f[i] for f in frames if f[i] is not None]
        return round(sum(v) / len(v), 1) if v else None

    def mx(i):
        v = [f[i] for f in frames if f[i] is not None]
        return round(max(v), 1) if v else None

    return {
        "frames": n, "seconds": round(total / 1000.0, 1),
        "avg_fps": round(1000.0 * n / total, 1),
        "low1_fps": round(1000.0 / (sum(worst) / len(worst)), 1),
        "frametime_mean_ms": round(mean, 2), "frametime_stdev_ms": round(sd, 2),
        "frametime_variance_ms2": round(sd * sd, 2), "frametime_cv": round(sd / mean, 3),
        "frametime_p50_ms": round(p50, 2), "frametime_p99_ms": round(_pct(s, 0.99), 2),
        "stutters": sum(1 for x in ft if x > 2.5 * p50),
        "gpu_clock_avg": avg(1), "gpu_temp_max": mx(2), "cpu_temp_max": mx(3), "power_avg": avg(4),
        "power_reliable": False, "log": os.path.basename(path),
    }


def mangohud_env(folder, delay_s=0, duration_s=0):
    """Launch variables for one measurement (no overlay on screen)."""
    opts = ["no_display", "log_interval=0", f"output_folder={folder}"]
    if delay_s or duration_s:
        opts.append(f"autostart_log={max(1, int(delay_s))}")
    else:
        opts.append("autostart_log=1")
    if duration_s:
        opts.append(f"log_duration={int(duration_s)}")
    return {"MANGOHUD": "1", "MANGOHUD_CONFIG": ",".join(opts)}
