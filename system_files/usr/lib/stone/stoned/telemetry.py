"""Adaptive telemetry (audit sections 7 and 13).

Nobody subscribed  -> no metric reads at all (only the thermal watch, see thermal.py).
Performance Center -> every 1 s.     Technical overlay in a game -> every 0.5 s.
Stability test     -> every 0.25 s (internal).

FPS, frametime and 1 % lows are NOT read here: they only exist inside the game's
render, and MangoHud / Steam's performance overlay already measure them. STONE does not
launch a second tool for the same data.
"""

import os
import time

from gi.repository import GLib

RATES_MS = {"performance_center": 1000, "overlay": 500, "test": 250}


def _root():
    return os.environ.get("STONE_SYSFS_ROOT", "/")


def _proc(name):
    return os.path.join(_root(), "proc", name)


class CpuUsage:
    """Whole-CPU busy % from /proc/stat deltas (needs two reads)."""
    def __init__(self):
        self.prev = None

    def read(self):
        try:
            with open(_proc("stat")) as f:
                parts = f.readline().split()
        except OSError:
            return None
        if not parts or parts[0] != "cpu":
            return None
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals[:8])
        prev, self.prev = self.prev, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def meminfo():
    out = {}
    try:
        with open(_proc("meminfo")) as f:
            for line in f:
                k, _, rest = line.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    out[k] = int(rest.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        return None, None
    if "MemTotal" not in out or "MemAvailable" not in out:
        return None, None
    return out["MemTotal"] - out["MemAvailable"], out["MemTotal"]


class Telemetry:
    def __init__(self, backend, emit):
        self.backend = backend
        self.emit = emit                 # callable(dict)
        self.subs = {}                   # sender -> context
        self.internal = set()            # contexts held by stoned itself ("test")
        self.cpu = CpuUsage()
        self.timer = 0
        self.rate = None
        self.last = None
        self.samples = 0
        self.cost_s = 0.0

    def sample(self):
        t0 = time.perf_counter()
        m = self.backend.metrics()
        m["cpu_pct"] = self.cpu.read()
        used, total = meminfo()
        m["ram_used_mb"], m["ram_total_mb"] = used, total
        m["t"] = round(time.time(), 2)
        m["missing"] = sorted(k for k, v in m.items() if v is None)
        self.cost_s += time.perf_counter() - t0
        self.samples += 1
        self.last = m
        return m

    def _wanted_rate(self):
        contexts = set(self.subs.values()) | self.internal
        rates = [RATES_MS[c] for c in contexts if c in RATES_MS]
        return min(rates) if rates else None

    def _rearm(self):
        rate = self._wanted_rate()
        if rate == self.rate:
            return
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.rate = rate
        if rate:
            self.cpu.read()              # prime the CPU delta
            self.timer = GLib.timeout_add(rate, self._tick)

    def _tick(self):
        self.emit(self.sample())
        return True

    def subscribe(self, sender, context):
        self.subs[sender] = context
        self._rearm()
        return self.status()

    def unsubscribe(self, sender):
        self.subs.pop(sender, None)
        self._rearm()
        return self.status()

    def hold(self, context, on):
        (self.internal.add if on else self.internal.discard)(context)
        self._rearm()

    def name_gone(self, name):
        if name in self.subs:
            self.unsubscribe(name)

    def status(self):
        return {"rate_ms": self.rate or 0, "subscribers": len(self.subs),
                "avg_sample_ms": round(1000 * self.cost_s / self.samples, 3) if self.samples else None}
