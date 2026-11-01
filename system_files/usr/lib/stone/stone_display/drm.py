"""What the kernel (DRM/KMS) knows about each output: /sys/class/drm/card*-<connector>/.

Readable by any user; no ioctl, no DRM master, nothing is changed.

  status   connected | disconnected | unknown
  modes    resolutions the kernel accepts on this connector, after its own checks against the
           link and any adapter (the first line is the display's preferred mode)
  edid     raw EDID bytes (parsed by edid.py)

Connector names (HDMI-A-1, DP-2, ...) are reported, never assumed.
"""

import glob
import os
import re

from . import edid as E

KIND = {"HDMI-A": "HDMI", "HDMI-B": "HDMI", "DP": "DisplayPort", "eDP": "internal panel", "DVI-D": "DVI",
        "DVI-I": "DVI", "VGA": "VGA", "Virtual": "virtual", "Writeback": "writeback"}


def _root():
    return os.environ.get("STONE_SYSFS_ROOT", "/")


def _read(path, binary=False):
    try:
        with open(path, "rb" if binary else "r") as f:
            return f.read() if binary else f.read().strip()
    except OSError:
        return None


def connector_kind(name):
    base = re.sub(r"-\d+$", "", name)
    return KIND.get(base, base or "unknown")


def connectors():
    """Every connector of every card: [{"card","connector","kind","status","enabled","modes","edid"}]."""
    out = []
    for d in sorted(glob.glob(os.path.join(_root(), "sys/class/drm/card[0-9]*-*"))):
        base = os.path.basename(d)
        m = re.match(r"(card\d+)-(.+)$", base)
        if not m or not os.path.isdir(d):
            continue
        card, name = m.groups()
        if name.startswith("Writeback"):
            continue
        modes = []
        for line in (_read(os.path.join(d, "modes")) or "").splitlines():
            mm = re.match(r"^(\d+)x(\d+)(i?)$", line.strip())
            if mm:
                mode = {"w": int(mm.group(1)), "h": int(mm.group(2)), "interlaced": bool(mm.group(3))}
                if mode not in modes:
                    modes.append(mode)
        raw = _read(os.path.join(d, "edid"), binary=True)
        out.append({"card": card, "connector": name, "kind": connector_kind(name),
                    "status": _read(os.path.join(d, "status")) or "unknown",
                    "enabled": _read(os.path.join(d, "enabled")) == "enabled",
                    "modes": modes, "edid": E.parse(raw) if raw else None,
                    "edid_present": bool(raw)})
    return out
