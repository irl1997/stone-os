"""Boot-time recovery: `stoned --recover` (oneshot, ordered after the GPU governor).

It never applies a performance setting. At this point the governor has just started
from its own factory configuration file, which STONE never edits, so the GPU is already
at BALANCED-equivalent values. This step only decides what stoned may do later, once
Home is on screen and the user can act.

Rules:
  * The previous session ended cleanly  -> failure counter back to 0.
  * It did not, and something risky was going on (a stability test, a slot that is
    not validated yet, or a boot that never reached Home) -> counter + 1, the slot
    being tried is marked failed, and the selected profile falls back to the last
    known good configuration.
  * Counter >= 2, or `stone.safe=1` on the kernel command line -> Safe Mode.
"""

import os
import time

from . import state as S

SAFE_FAILS = 2


def kernel_requests_safe_mode(cmdline_path=None):
    cmdline_path = cmdline_path or os.environ.get("STONE_CMDLINE", "/proc/cmdline")   # env: tests only
    try:
        with open(cmdline_path) as f:
            return "stone.safe=1" in f.read().split()
    except OSError:
        return False


def recover(store, cmdline_path=None, now=None):
    now = now or time.time()
    st = store.load()
    clean = store.get_flag(S.FLAG_CLEAN) is not None
    trial = store.get_flag(S.FLAG_TRIAL)
    provisional = store.get_flag(S.FLAG_PROVISIONAL)
    boot_unfinished = store.get_flag(S.FLAG_BOOT) is not None
    first_boot = not store.has_state_file() and not boot_unfinished and trial is None and provisional is None

    report = {"clean": clean or first_boot, "actions": []}

    if clean or first_boot:
        st["fail_count"] = 0
    else:
        risky = trial is not None or provisional is not None or boot_unfinished \
            or S.slot_index(st["profile"]) is not None
        if risky:
            st["fail_count"] += 1
            failed = None
            if trial is not None:
                # A trial only ever applies draft values; saved slots are untouched.
                failed = trial.get("profile") if S.slot_index(trial.get("profile")) is not None else None
            if provisional is not None:
                k = S.slot_index(provisional.get("profile"))
                if k is not None and st["slots"][k] is not None and st["slots"][k]["state"] != S.SLOT_VALIDATED:
                    st["slots"][k]["state"] = S.SLOT_FAILED
                    report["actions"].append(f"slot c{k + 1} marked failed")
                    failed = f"c{k + 1}"
            if failed is None and S.slot_index(st["profile"]) is not None:
                failed = st["profile"]   # a CUSTOM slot (even validated) was active
            if trial is not None:
                report["actions"].append("stability test discarded")
            st["last_failure"] = {
                "time": int(now),
                "during": "test" if trial is not None else "provisional" if provisional is not None else "boot",
                "profile": failed,
            }
            if not S.is_safe_to_apply(st["profile"], st["slots"]) or failed == st["profile"]:
                if st["last_known_good"] == failed:
                    st["last_known_good"] = S.DEFAULT_PROFILE
                st["profile"] = st["last_known_good"]
                report["actions"].append(f"profile -> last known good ({st['last_known_good']})")
        else:
            # Unclean, but nothing STONE tuned was in play (factory profile, Home had come
            # up): e.g. a game crash at BALANCED. Not counted against tuning; still recorded.
            st["last_failure"] = {"time": int(now), "during": "session", "profile": st["profile"]}

    # Requests left by `stonectl` when the service could not be reached.
    if store.get_flag(S.FLAG_SAFE_REQUEST) is not None:
        st["safe_request"] = True
        report["actions"].append("safe mode requested from the command line")
    if store.get_flag(S.FLAG_SAFE_EXIT) is not None:
        st["safe_request"] = False
        st["fail_count"] = 0
        report["actions"].append("safe mode exit requested from the command line")
    store.clear_flag(S.FLAG_SAFE_REQUEST)
    store.clear_flag(S.FLAG_SAFE_EXIT)

    kernel = kernel_requests_safe_mode(cmdline_path)
    st["safe_reason"] = "kernel" if kernel else "manual" if st["safe_request"] \
        else "failures" if st["fail_count"] >= SAFE_FAILS else None
    st["safe_mode"] = st["safe_reason"] is not None
    if st["safe_mode"]:
        report["actions"].append(f"safe mode ({st['safe_reason']})")

    # The selected profile must always be something that can be applied without asking.
    if not S.is_safe_to_apply(st["profile"], st["slots"]):
        st["profile"] = st["last_known_good"]
        report["actions"].append("unvalidated profile not kept across boot")

    store.save(st)
    for f in (S.FLAG_TRIAL, S.FLAG_PROVISIONAL, S.FLAG_CLEAN):
        store.clear_flag(f)
    store.set_flag(S.FLAG_BOOT, {"time": int(now)})
    report["fail_count"] = st["fail_count"]
    report["safe_mode"] = st["safe_mode"]
    report["safe_reason"] = st["safe_reason"]
    report["profile"] = st["profile"]
    return report


def mark_clean_shutdown(store):
    """Orderly shutdown only (ExecStop of stoned-recover.service).

    Trial / provisional flags are left alone: an orderly shutdown proves nothing about
    an unvalidated setting, and the next boot never keeps one applied anyway."""
    store.set_flag(S.FLAG_CLEAN, {"time": int(time.time())})
