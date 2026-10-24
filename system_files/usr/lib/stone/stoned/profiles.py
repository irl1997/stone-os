"""Profiles and input validation.

Every value coming from the UI is re-checked here (type, range, hardware, mode,
thermal state). The UI only offers valid values, but it is never trusted.
"""

from . import state as S
from .errors import StoneError, INVALID_VALUE, UNSUPPORTED, THERMAL_LIMIT, SAFE_MODE, BUSY

# Factory profiles. Same values as the prototype and the audit (section 5).
# BALANCED equals the governor's own factory configuration.
FACTORY = {
    "silent": {"max": 1500, "min": 1000, "temp": 75, "resp": 0},
    "balanced": {"max": 1850, "min": 1000, "temp": 85, "resp": 1},
    "performance": {"max": 2000, "min": 1000, "temp": 85, "resp": 1},
}

# STONE's own bounds. They are intersected with what the governor reports in
# Range/Allowed, so STONE can never ask for more than the governor's safe curve.
STONE_MAX = (1500, 2000)
STONE_MIN = (1000, 1500)
STONE_TEMP = (65, 85)          # STONE can lower the throttling point, never raise it
RECOVERY_GAP = 10              # recovery = limit - 10 C, as the governor's factory file
GPU_WARN_C = 85                # above this, requests that raise the ceiling are refused

# "Respuesta del regulador" -> governor load target (upper, lower), as fractions.
# Normal = the governor's shipped values (0.65 / 0.50). Suave and Rapida are a STONE
# proposal: they only change when the governor steps up, never its frequency or
# voltage limits. [VERIFICAR en placa]
RESPONSE = {0: (0.80, 0.65), 1: (0.65, 0.50), 2: (0.50, 0.35)}

TEST_LEVELS = {"quick": 300, "standard": 1800, "extended": 7200}   # seconds


def resolve(pid, st):
    """Profile id -> concrete values, or raise."""
    if pid in FACTORY:
        return dict(FACTORY[pid])
    k = S.slot_index(pid)
    if k is None or st["slots"][k] is None:
        raise StoneError(INVALID_VALUE, f"unknown profile {pid!r}")
    s = st["slots"][k]
    return {"max": s["max"], "min": s["min"], "temp": s["temp"], "resp": s["resp"]}


def _int(values, key):
    v = values.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise StoneError(INVALID_VALUE, f"{key} must be an integer")
    return v


def validate_values(values, caps):
    """Check a CUSTOM draft. Returns the normalized dict or raises INVALID_VALUE."""
    if not isinstance(values, dict):
        raise StoneError(INVALID_VALUE, "values must be a dictionary")
    unknown = set(values) - {"max", "min", "temp", "resp"}
    if unknown:
        raise StoneError(INVALID_VALUE, f"unknown keys: {sorted(unknown)}")
    mx, mn, temp = _int(values, "max"), _int(values, "min"), _int(values, "temp")
    resp = _int(values, "resp") if "resp" in values else 1

    lo, hi = caps["gpu_range"]   # from the governor's Range/Allowed
    if not (max(STONE_MAX[0], lo) <= mx <= min(STONE_MAX[1], hi)):
        raise StoneError(INVALID_VALUE, f"max {mx} MHz outside the supported range")
    if not (max(STONE_MIN[0], lo) <= mn <= min(STONE_MIN[1], hi)):
        raise StoneError(INVALID_VALUE, f"min {mn} MHz outside the supported range")
    if mn > mx:
        raise StoneError(INVALID_VALUE, "min must not exceed max")
    if mx % 50 or mn % 100:
        raise StoneError(INVALID_VALUE, "max goes in 50 MHz steps, min in 100 MHz steps")
    if not (STONE_TEMP[0] <= temp <= STONE_TEMP[1]) or temp % 5:
        raise StoneError(INVALID_VALUE, f"temperature limit {temp} C outside 65-85 C in 5 C steps")
    if resp not in RESPONSE:
        raise StoneError(INVALID_VALUE, "response must be 0, 1 or 2")
    return {"max": mx, "min": mn, "temp": temp, "resp": resp}


def check_can_apply(values, caps, st, current, gpu_temp, testing, thermal_reduced=False):
    """Mode, hardware and thermal checks for anything about to reach the governor."""
    if not caps.get("governor"):
        raise StoneError(UNSUPPORTED, "GPU governor not available")
    if testing:
        raise StoneError(BUSY, "a stability test is running")
    if st["safe_mode"] and (values["max"] > FACTORY["balanced"]["max"] or values["temp"] > FACTORY["balanced"]["temp"]):
        raise StoneError(SAFE_MODE, "Safe Mode: nothing above BALANCED")
    if thermal_reduced and values["max"] > FACTORY["silent"]["max"]:
        raise StoneError(THERMAL_LIMIT, "performance reduced by the thermal watch until the GPU cools down")
    raises_ceiling = current is None or values["max"] > current.get("max", 0)
    if raises_ceiling and gpu_temp is not None and gpu_temp >= GPU_WARN_C:
        raise StoneError(THERMAL_LIMIT, f"GPU at {gpu_temp} C")


def governor_calls(values):
    """Exactly what will be sent to the governor for a set of values."""
    up, low = RESPONSE[values["resp"]]
    return {
        "range": (values["min"], values["max"]),
        "temperature": (values["temp"], values["temp"] - RECOVERY_GAP),
        "load_target": (low, up),
    }
