"""STONE EXPERIENCE SCORE (internal, 0..100): best experience, not the biggest number.

    SCORE = A·wA + S·wS + Q·wQ + H·wH + E·wE      (each part 0..1, weights per mode add to 100)

  A  target achievement   0.6·min(1, avg_fps / T) + 0.4·min(1, low1_fps / (0.75·T))
                          T = the mode's experience target for this display (targets())
  S  frame stability      clamp(1 − 4·max(0, cv − 0.05)) · clamp(1 − 0.5·max(0, p99/p50 − 1.3))
                          · clamp(1 − stutters_per_minute / 30)
  Q  image strategy       reconstruction(path) · (0.55 + 0.45·internal_scale)   — a PRIOR, not a
                          measurement: native 1.0, temporal upscalers (game FSR/XeSS, HelixSR,
                          OptiScaler) 0.80 each, Gamescope spatial 0.60. When the person rated
                          the image on screen (1..5), Q = rating / 5 replaces the prior.
  H  thermal              min(1 − (gpu_temp_max − 75)/15, 1 − (cpu_temp_max − 85)/10), clamped;
                          unknown temperature = 0.5 (no credit for what was not measured)
  E  efficiency           lowest average GPU clock among this game's runs / this run's (1 = the
                          most frugal run); unknown = 0.5

  weights        A    S    Q    H    E
  recommended    35   30   20   10    5
  quality        25   25   35   10    5
  performance    40   35   10   10    5

GATES (a number never hides an unplayable run):
  abnormal exit (crash)                       -> 0
  avg_fps < 0.5·T or low1_fps < 0.4·T         -> at most 40
  gpu_temp_max ≥ 90 or cpu_temp_max ≥ 95      -> at most 30
  not enough frames                           -> None (no score)

PREFERENCES (optimizer.recommend):
  EFFICIENCY  runs within EQUAL_POINTS of the best experience (score without E) count as equal:
              the one with the lowest average GPU clock wins (40 CU @ 1600 MHz over 2000 MHz).
  HELIXSR     among equals after that, HelixSR is the preferred candidate. Never forced: it must
              have been measured on this console and reached the same experience.
"""

TARGET_FRACTION_LOW = 0.75
EQUAL_POINTS = 2.0
WEIGHTS = {
    "recommended": {"A": 35, "S": 30, "Q": 20, "H": 10, "E": 5},
    "quality": {"A": 25, "S": 25, "Q": 35, "H": 10, "E": 5},
    "performance": {"A": 40, "S": 35, "Q": 10, "H": 10, "E": 5},
}
WEIGHTS["custom"] = WEIGHTS["recommended"]
RECON = {"native": 1.0, "game_upscaler": 0.80, "helixsr": 0.80, "optiscaler": 0.80, "gamescope": 0.60}


def _c(x):
    return max(0.0, min(1.0, x))


def cadences(hz):
    """Frame rates that divide the refresh evenly (no judder without VRR), ≥ 30."""
    hz = int(round(hz or 60))
    return sorted({hz // d for d in range(1, 9) if hz % d == 0 and hz // d >= 30}, reverse=True) or [30]


def targets(display):
    """Experience target T per mode, from the REAL display (refresh, VRR). No rigid 60:
    a 120 Hz TV with VRR gets 40 for QUALITY (smooth inside the VRR range) and up to 90 for
    PERFORMANCE; a 60 Hz TV without VRR gets 60 / 30 (even cadences only)."""
    hz = (display or {}).get("hz") or 60
    vrr = (display or {}).get("vrr") or {}
    vrr_on = bool(vrr.get("supported")) and bool(vrr.get("range"))
    lo = (vrr.get("range") or [48, hz])[0] if vrr_on else None
    cad = cadences(hz)
    if vrr_on:
        perf = min(hz, 90)
        rec = 60 if hz >= 60 else hz
        qual = max(lo or 40, 40) if hz >= 80 else max(lo or 30, 30)
    else:
        perf = next((c for c in cad if c <= 90), cad[-1])
        rec = next((c for c in cad if c <= 60), cad[-1])
        qual = next((c for c in cad if c <= 40), cad[-1])
    out = {"performance": perf, "recommended": rec, "quality": min(qual, rec), "custom": rec}
    return {"targets": out, "vrr": vrr_on, "vrr_min": lo, "hz": hz, "cadences": cad}


def sustained(m, display):
    """The highest frame rate this run holds: the cap to play it at (None if below 30)."""
    t = targets(display)
    if t["vrr"]:
        for c in range(min(t["hz"], 120), 29, -1):
            if m["avg_fps"] >= c * 1.05 and m["low1_fps"] >= max(t["vrr_min"] or 30, 0.8 * c):
                return c
        return None
    for c in t["cadences"]:
        if m["low1_fps"] >= 0.92 * c:
            return c
    return None


def quality_prior(config):
    g = config["graphics"]
    return RECON[g["path"]] * (0.55 + 0.45 * g["internal"]["scale"])


def parts(m, config, target, ref_clock=None, image_rating=None):
    A = 0.6 * _c(m["avg_fps"] / target) + 0.4 * _c(m["low1_fps"] / (TARGET_FRACTION_LOW * target))
    p99r = (m["frametime_p99_ms"] / m["frametime_p50_ms"]) if m.get("frametime_p50_ms") else 2.0
    spm = m.get("stutters", 0) / max(m.get("seconds", 60) / 60.0, 1e-6)
    S = _c(1 - 4 * max(0.0, m["frametime_cv"] - 0.05)) * _c(1 - 0.5 * max(0.0, p99r - 1.3)) * _c(1 - spm / 30.0)
    Q = _c(image_rating / 5.0) if image_rating else quality_prior(config)
    hs = []
    if m.get("gpu_temp_max") is not None:
        hs.append(1 - (m["gpu_temp_max"] - 75) / 15.0)
    if m.get("cpu_temp_max") is not None:
        hs.append(1 - (m["cpu_temp_max"] - 85) / 10.0)
    H = _c(min(hs)) if hs else 0.5
    clk = m.get("gpu_clock_avg")
    E = _c(ref_clock / clk) if clk and ref_clock else 0.5
    return {"A": round(A, 3), "S": round(S, 3), "Q": round(Q, 3), "H": round(H, 3), "E": round(E, 3)}


def score(m, config, mode, target, ref_clock=None, image_rating=None, crashed=False):
    """{"score", "experience", "parts", "gate"}; score None when the data cannot support one."""
    if crashed:
        return {"score": 0.0, "experience": 0.0, "parts": None, "gate": "abnormal exit"}
    if not m or m.get("error"):
        return {"score": None, "experience": None, "parts": None, "gate": (m or {}).get("error", "no data")}
    w = WEIGHTS[mode]
    p = parts(m, config, target, ref_clock, image_rating)
    total = sum(p[k] * w[k] for k in w)
    experience = sum(p[k] * w[k] for k in w if k != "E") * 100.0 / (100 - w["E"])
    gate = None
    if m["avg_fps"] < 0.5 * target or m["low1_fps"] < 0.4 * target:
        gate, total, experience = "far below target", min(total, 40.0), min(experience, 40.0)
    if (m.get("gpu_temp_max") or 0) >= 90 or (m.get("cpu_temp_max") or 0) >= 95:
        gate, total, experience = "too hot", min(total, 30.0), min(experience, 30.0)
    return {"score": round(total, 1), "experience": round(experience, 1), "parts": p, "gate": gate}
