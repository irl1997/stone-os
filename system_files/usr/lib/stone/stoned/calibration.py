"""STONE CALIBRATION: what THIS BC-250 sustains, measured on it, never assumed.

Performance range (all in MHz, GPU ceiling):

  hardware   what the GPU governor allows on this machine (its Range/Allowed: the voltage curve
             the cyan-skillfish-governor-smu package ships). STONE never asks for more. Voltage
             is never changed by STONE.
  baseline   the safe start, always allowed. 40 CU: 1500 MHz (the unlock's author measured 83 C
             there and recommends it); 24 CU: the governor's own factory profile.
  validated  the highest ceiling THIS machine passed calibration at, in the configuration it
             runs now (CU count, kernel, amdgpu, governor, VBIOS, test version). Without a valid
             calibration: 24 CU = the governor's factory maximum (the shipping configuration);
             40 CU = the baseline.
  current    what the governor reports right now (read back, never the requested value).

Calibration, started by the person (never by STONE), one step at a time:

  LAST KNOWN GOOD saved -> flag written (a crash is attributed to this step) -> step applied
  temporarily -> load: stone-cu-verify (GPU compute checked against the CPU, every CU busy),
  vkmark if it is missing -> checks: no wrong results, no amdgpu error in the kernel log, GPU
  never above the limit, clock sustained -> next step (+STEP_MHZ) ... up to `hardware`.

  First failure (or crash at boot): validated = last passed step - MARGIN_MHZ. The failing
  frequency shows where the edge is; one step below is kept as margin. Reaching the hardware
  maximum without a failure validates it as is. A thermal stop validates the last step that
  stayed under the limit. Nothing is applied at boot that did not pass here.
"""

import hashlib
import os
import time

CAL_VERSION = 1
STEP_MHZ = 50
MARGIN_MHZ = 50
# The machine went down (power cut, hang) at a step: the most serious failure there is. The
# validated maximum stays at least CRASH_MARGIN_MHZ below the step it went down at (and, as
# always, MARGIN_MHZ below the last step that passed), and no later calibration of this machine
# in this configuration goes back to that step (board, rc15: total hang at 2000 MHz, 40 CU; fans kept
# running, nothing reached the log).
CRASH_MARGIN_MHZ = 100
TEMP_LIMIT_C = 85                 # STONE never raises the governor's throttling point above this
BASELINE_40CU = 1500
BALANCED_CAP = 1850               # BALANCED: the governor's own factory value, at most
STEP_SECONDS = {"quick": 90, "standard": 300}
VERIFY = os.environ.get("STONE_CU_VERIFY", "/usr/libexec/stone/stone-cu-verify")
RELEASE = os.environ.get("STONE_RELEASE_FILE", "/usr/lib/stone-release")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _release():
    out = {}
    for line in (_read(RELEASE) or "").splitlines():
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


def configuration(backend, caps):
    """What a calibration result depends on. Read from sysfs and the image, no root."""
    card = (getattr(backend, "card", None) or {}).get("device")
    root = os.environ.get("STONE_SYSFS_ROOT", "/")
    rel = _release()
    cu = 40 if (caps.get("cu40") or {}).get("active") else 24
    return {
        "cu": cu,
        "vbios": _read(os.path.join(card, "vbios_version")) if card else None,
        "kernel": os.uname().release if root == "/" else _read(os.path.join(root, "proc/sys/kernel/osrelease")),
        "amdgpu": _read(os.path.join(root, "sys/module/amdgpu/srcversion")),
        "governor": rel.get("STONE_GOVERNOR_VERSION"),
        "test": CAL_VERSION,
    }


def machine_id(backend):
    """THIS machine: PCI address + device + VBIOS, hashed (nothing personal is stored)."""
    card = (getattr(backend, "card", None) or {})
    dev = card.get("device") or ""
    key = f"{os.path.basename(os.path.realpath(dev)) if dev else '?'}|{card.get('pci_id')}|{_read(os.path.join(dev, 'vbios_version')) if dev else None}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# What makes an old result stale (each with the reason shown to the person).
STALE_KEYS = (("cu", "CU count changed"), ("vbios", "VBIOS changed"), ("governor", "GPU governor updated"),
              ("kernel", "kernel updated"), ("amdgpu", "amdgpu driver changed"), ("test", "calibration test changed"))


def status(record, config, mid):
    """A stored result judged against the machine as it is now."""
    if not record:
        return {"state": "none", "reasons": []}
    if record.get("machine") != mid:
        return {"state": "stale", "reasons": ["different machine"]}
    old = record.get("config") or {}
    reasons = [why for key, why in STALE_KEYS if old.get(key) != config.get(key)]
    if reasons:
        return {"state": "stale", "reasons": reasons}
    if record.get("stopped") == "load_too_light":
        return {"state": "invalid", "reasons": ["the test load did not use the GPU (not a GPU failure)"]}
    if record.get("validated_max") is None:
        return {"state": "failed", "reasons": [record.get("stopped") or "baseline did not pass"]}
    return {"state": record.get("result", "complete"), "reasons": []}


def performance_range(caps, record, config, mid):
    """{hardware, baseline, validated_max, validated_source, cu, calibration}."""
    lo, hi = caps.get("hardware_range") or caps.get("gpu_range") or (0, 0)
    cu = config["cu"]
    st = status(record, config, mid)
    if cu == 40:
        baseline = min(BASELINE_40CU, hi) if hi else BASELINE_40CU
        fallback, fsrc = baseline, "baseline (40 CU without calibration)"
    else:
        baseline = min(BASELINE_40CU, hi) if hi else BASELINE_40CU
        fallback, fsrc = hi, "governor factory curve (24 CU)"
    if st["state"] in ("complete", "partial", "thermal") and record.get("validated_max"):
        validated, src = min(record["validated_max"], hi) if hi else record["validated_max"], "calibration"
    else:
        validated, src = fallback, fsrc
    validated = max(validated, baseline) if validated else validated
    return {"hardware": [lo, hi], "baseline": baseline, "validated_max": validated, "validated_source": src,
            "cu": cu, "calibration": dict(st, time=(record or {}).get("time"),
                                          result=(record or {}).get("validated_max"),
                                          stopped=(record or {}).get("stopped"))}


def steps(start, hardware_hi):
    out, f = [], start
    while f <= hardware_hi:
        out.append(f)
        f += STEP_MHZ
    if out and out[-1] != hardware_hi and hardware_hi > start:
        out.append(hardware_hi)
    return out


def validated_from(passed, failed_at, hardware_hi, margin=MARGIN_MHZ):
    """passed: steps that passed, in order; failed_at: the step that failed (None = none failed)."""
    if not passed:
        return None
    top = max(passed)
    if failed_at is None and top >= hardware_hi:
        return top
    return max(min(passed), top - margin) if failed_at is not None else top


def crash_cap(crash, config, mid):
    """The step this machine went down at, if it still applies (same machine and configuration)."""
    if not crash or crash.get("machine") != mid:
        return None
    old = crash.get("config") or {}
    if any(old.get(k) != config.get(k) for k, _ in STALE_KEYS):
        return None
    return crash.get("mhz")


def workload(seconds):
    """The load of each step. stone-cu-verify checks every result against the CPU, but between
    its GPU passes the GPU waits for that check, and an idle GPU lets the governor drop the
    clock; so the stability load (vkmark) runs next to it and keeps the GPU at the ceiling.
    A wrong result ends stone-cu-verify with status 2 (step fails); the whole group is stopped
    together. Without stone-cu-verify: the stability load alone."""
    import shlex
    from .stability import workload_cmd
    spv = VERIFY + ".spv"
    if os.access(VERIFY, os.X_OK) and os.path.exists(spv):
        load = " ".join(shlex.quote(a) for a in workload_cmd())
        return (["/bin/sh", "-c", f'{load} >/dev/null 2>&1 & exec "$0" "$@"', VERIFY, spv, "--seconds", str(seconds + 60)],
                "stone-cu-verify + vkmark")
    return None, "vkmark"


def record(mid, config, level, steps_done, failed_at, stopped, hardware_hi, workload_name):
    passed = [s["mhz"] for s in steps_done if s["passed"]]
    v = validated_from(passed, failed_at, hardware_hi)
    if stopped in ("crash", "crash_cap") and v is not None and isinstance(failed_at, int):
        v = max(min(passed), min(v, failed_at - CRASH_MARGIN_MHZ))
    result = "complete" if failed_at is None and stopped is None else \
        {"thermal": "thermal", "load_too_light": "invalid"}.get(stopped, "partial")
    return {"machine": mid, "config": config, "level": level, "time": int(time.time()), "version": CAL_VERSION,
            "workload": workload_name, "steps": steps_done[-24:], "failed_at": failed_at, "stopped": stopped,
            "validated_max": v, "result": result,
            "max_temp_c": max([s.get("max_temp_c") or 0 for s in steps_done] or [0]) or None}


def interrupted(prev, mid, config, level, steps_done, workload_name, crash=None):
    """A calibration that stopped without failing (clean shutdown, service restart): nothing
    failed, so the step that was running is not a failure and gets no margin. The steps that
    passed are kept only if they validate more than the result already stored (board, rc16: the
    idle power-off 30 min into a standard calibration must not lower 1900 MHz to its first steps).
    Returns the record to store, or None to keep the previous one."""
    passed = [s["mhz"] for s in steps_done if s.get("passed")]
    if not passed:
        return None
    v = max(passed)
    cap = crash_cap(crash, config, mid)
    if cap is not None:
        v = max(min(passed), min(v, cap - CRASH_MARGIN_MHZ))
    prev_ok = prev and status(prev, config, mid)["state"] in ("complete", "partial", "thermal")
    if prev_ok and (prev.get("validated_max") or 0) >= v:
        return None
    return {"machine": mid, "config": config, "level": level, "time": int(time.time()), "version": CAL_VERSION,
            "workload": workload_name, "steps": steps_done[-24:], "failed_at": None, "stopped": "interrupted",
            "validated_max": v, "result": "partial",
            "max_temp_c": max([s.get("max_temp_c") or 0 for s in steps_done] or [0]) or None}
