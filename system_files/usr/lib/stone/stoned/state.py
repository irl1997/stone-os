"""Persistent state of stoned.

Everything STONE remembers about performance lives in one directory owned by the
`stoned` user (default /var/lib/stone, 0700). /var is not touched by bootc / rpm-ostree
updates, so the state survives OS updates; deleting the directory is a full factory reset.

Two kinds of files:

* state.json  - durable settings (selected profile, CUSTOM slots, last known good,
                per-game profiles, failure counter). Written atomically.
* flags/*     - small marker files that describe what was happening when the machine
                went down. They are written *before* the risky action starts, so a
                power cut, kernel panic or GPU hang always leaves them behind.
"""

import json
import os
import tempfile

SCHEMA = 1
FACTORY_PROFILES = ("silent", "balanced", "performance")
DEFAULT_PROFILE = "balanced"
SLOT_COUNT = 3

# Slot lifecycle. Only "validated" slots may ever be applied without the user asking for
# it in the current boot (P0-3).
SLOT_TESTED = "tested"        # passed a stability test, never finished a game session
SLOT_VALIDATED = "validated"  # passed STANDARD + one game session that ended normally
SLOT_FAILED = "failed"        # the machine went down while this slot was being tried

FLAG_BOOT = "boot-in-progress"        # set by --recover, cleared once Home has been up
FLAG_TRIAL = "trial"                  # a stability test is applying draft values
FLAG_PROVISIONAL = "provisional"      # a not-yet-validated slot is applied right now
FLAG_CLEAN = "clean-shutdown"         # written by the orderly shutdown path only
# Written by `stonectl` (as root) when stoned cannot be reached; read at the next boot.
FLAG_SAFE_REQUEST = "safe-request"
FLAG_SAFE_EXIT = "safe-exit"


def state_dir():
    return os.environ.get("STONE_STATE_DIR", "/var/lib/stone")


def default_state():
    return {
        "schema": SCHEMA,
        "profile": DEFAULT_PROFILE,      # what the user selected globally
        "last_known_good": DEFAULT_PROFILE,
        "slots": [None] * SLOT_COUNT,
        "per_game": {},                  # Steam AppID (string) -> profile id
        "fail_count": 0,
        "safe_mode": False,
        "safe_request": False,           # Safe Mode asked for by the user; stays until they leave it
        "safe_reason": None,             # "failures" | "kernel" | "manual"
        "last_failure": None,
        "tests": [],                     # recent stability-test results (BEFORE / AFTER)
    }


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path, data):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(d)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _clean_slot(s):
    """Return a well-formed slot or None. Anything malformed is dropped, never trusted."""
    if not isinstance(s, dict):
        return None
    try:
        out = {
            "max": int(s["max"]), "min": int(s["min"]), "temp": int(s["temp"]),
            "resp": int(s.get("resp", 1)), "state": str(s["state"]),
            "test_level": str(s.get("test_level", "")),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if out["state"] not in (SLOT_TESTED, SLOT_VALIDATED, SLOT_FAILED):
        return None
    return out


def _sanitize(raw):
    st = default_state()
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return st
    slots = raw.get("slots")
    if isinstance(slots, list):
        st["slots"] = [_clean_slot(s) for s in slots[:SLOT_COUNT]] + [None] * max(0, SLOT_COUNT - len(slots))
    if isinstance(raw.get("per_game"), dict):
        st["per_game"] = {str(k): str(v) for k, v in raw["per_game"].items() if str(k).isdigit()}
    for key in ("fail_count",):
        try:
            st[key] = max(0, int(raw.get(key, 0)))
        except (TypeError, ValueError):
            pass
    st["safe_mode"] = bool(raw.get("safe_mode", False))
    st["safe_request"] = bool(raw.get("safe_request", False))
    st["safe_reason"] = raw.get("safe_reason") if raw.get("safe_reason") in ("failures", "kernel", "manual") else None
    st["last_failure"] = raw.get("last_failure") if isinstance(raw.get("last_failure"), dict) else None
    if isinstance(raw.get("tests"), list):
        st["tests"] = [t for t in raw["tests"] if isinstance(t, dict)][-10:]
    for key in ("profile", "last_known_good"):
        if is_profile_id(raw.get(key), st["slots"]):
            st[key] = raw[key]
    # The last known good must always be something that can be applied safely.
    if not is_safe_to_apply(st["last_known_good"], st["slots"]):
        st["last_known_good"] = DEFAULT_PROFILE
    return st


def slot_index(pid):
    if isinstance(pid, str) and len(pid) == 2 and pid[0] == "c" and pid[1].isdigit():
        k = int(pid[1]) - 1
        if 0 <= k < SLOT_COUNT:
            return k
    return None


def is_profile_id(pid, slots):
    if pid in FACTORY_PROFILES:
        return True
    k = slot_index(pid)
    return k is not None and slots[k] is not None


def is_safe_to_apply(pid, slots):
    """Factory profiles and validated slots. Used for anything applied automatically."""
    if pid in FACTORY_PROFILES:
        return True
    k = slot_index(pid)
    return k is not None and slots[k] is not None and slots[k]["state"] == SLOT_VALIDATED


class Store:
    def __init__(self, directory=None):
        self.dir = directory or state_dir()
        self.flags = os.path.join(self.dir, "flags")
        os.makedirs(self.flags, mode=0o700, exist_ok=True)
        self.path = os.path.join(self.dir, "state.json")

    # durable state
    def load(self):
        try:
            with open(self.path) as f:
                return _sanitize(json.load(f))
        except FileNotFoundError:
            return default_state()
        except (OSError, ValueError):
            # Corrupt file: keep a copy for diagnosis and start from factory values.
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass
            return default_state()

    def save(self, st):
        _atomic_write(self.path, json.dumps(_sanitize(st), indent=1, sort_keys=True))

    # flags
    def _flag(self, name):
        return os.path.join(self.flags, name)

    def set_flag(self, name, payload=None):
        _atomic_write(self._flag(name), json.dumps(payload or {}))

    def get_flag(self, name):
        try:
            with open(self._flag(name)) as f:
                v = json.load(f)
                return v if isinstance(v, dict) else {}
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {}   # present but unreadable still counts as "present"

    def clear_flag(self, name):
        try:
            os.unlink(self._flag(name))
            _fsync_dir(self.flags)
        except FileNotFoundError:
            pass

    def has_state_file(self):
        return os.path.exists(self.path)
