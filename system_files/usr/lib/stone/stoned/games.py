"""Steam game sessions, detected by stoned itself (audit section 5, plan step 10).

Steam starts every game, including non-Steam shortcuts it launches, under its `reaper`
helper with the command line `reaper SteamLaunch AppId=<id> -- ...`. Gamescope identifies
games the same way (it reads the AppId from its ancestor `reaper` process). stoned only
reads /proc/<pid>/cmdline, which any user may read; it needs no access to the user's
home, Steam's files or the game's environment. [VERIFICAR en placa con la versión de
Steam de la imagen: `ps -eo args | grep SteamLaunch`]

Not detected: games started outside Steam (e.g. Heroic launched on its own). They run
with the global profile.

A session ends when its reaper process is gone - whatever the reason: the game quit, it
crashed, or Steam itself went down. It counts as a *clean* session (the one that can
validate a CUSTOM slot) only if it lasted at least MIN_CLEAN_S and the kernel log shows
no amdgpu error since it started. A process that just vanished cannot tell a quit from a
crash, so the GPU log is the criterion, and an unreadable log never validates.
"""

import os
import time

from . import stability

POLL_S = 2
MIN_CLEAN_S = 600     # 10 min of play before a session may validate a slot


def _root():
    return os.environ.get("STONE_SYSFS_ROOT", "/")


def _proc():
    return os.path.join(_root(), "proc")


# STONE Home itself is a non-Steam shortcut, so Steam starts it under a reaper too. It is
# the console's interface, not a game: it never gets a per-game profile and never hides a
# real game launched from it.
NOT_GAMES = {"stone-home"}


def parse_reaper(argv):
    """['/.../reaper', 'SteamLaunch', 'AppId=570', '--', ...] -> '570', else None."""
    if not argv or os.path.basename(argv[0]) != "reaper" or "SteamLaunch" not in argv[1:3]:
        return None
    if "--" in argv:
        rest = argv[argv.index("--") + 1:]
        if any(os.path.basename(a.strip('"')) in NOT_GAMES for a in rest[:8]):
            return None
    for a in argv[1:6]:
        if a.startswith("AppId="):
            v = a[6:]
            return v if v.isdigit() and 0 < len(v) <= 12 else None
    return None


def read_cmdline(pid):
    try:
        with open(os.path.join(_proc(), pid, "cmdline"), "rb") as f:
            raw = f.read(4096)
    except OSError:
        return None
    return [x.decode("utf-8", "replace") for x in raw.split(b"\0") if x]


class SteamWatch:
    """Polls /proc every POLL_S seconds, reading cmdline only for pids it has not seen."""

    def __init__(self, on_start, on_end, clock=time.monotonic, wall=time.time):
        self.on_start, self.on_end = on_start, on_end
        self.clock, self.wall = clock, wall
        self.seen = set()            # pids already classified (not a reaper, or tracked)
        self.games = {}              # pid -> {"appid", "t0", "wall0"}
        self.current = None          # pid of the session stoned is applying profiles for
        self.scans = 0
        self.cost_s = 0.0
        try:
            self.min_clean = int(os.environ.get("STONE_MIN_SESSION_S", MIN_CLEAN_S))   # tests only
        except ValueError:
            self.min_clean = MIN_CLEAN_S

    def scan(self):
        t0 = time.perf_counter()
        try:
            pids = {p for p in os.listdir(_proc()) if p.isdigit()}
        except OSError:
            return
        self.seen &= pids                                    # forget pids that are gone
        for pid in sorted(pids - self.seen, key=int):
            self.seen.add(pid)
            appid = parse_reaper(read_cmdline(pid))
            if appid:
                self.games[pid] = {"appid": appid, "t0": self.clock(), "wall0": self.wall()}
                if self.current is None or self.current not in pids:
                    self.current = pid
                    self.on_start(appid)
        for pid in [p for p in self.games if p not in pids]:
            g = self.games.pop(pid)
            if pid == self.current:
                self.current = None
                self.on_end(g["appid"], self._clean(g))
                # another game still running (rare): it becomes the current session
                if self.games:
                    nxt = max(self.games, key=lambda p: self.games[p]["t0"])
                    self.current = nxt
                    self.on_start(self.games[nxt]["appid"])
        self.scans += 1
        self.cost_s += time.perf_counter() - t0

    def _clean(self, g):
        if self.clock() - g["t0"] < self.min_clean:
            return False
        errs = stability.kernel_errors_since(g["wall0"])
        return errs is not None and not errs

    def status(self):
        g = self.games.get(self.current) if self.current else None
        return {"source": "steam", "running": g["appid"] if g else None,
                "seconds": round(self.clock() - g["t0"]) if g else None,
                "avg_scan_ms": round(1000 * self.cost_s / self.scans, 3) if self.scans else None}
