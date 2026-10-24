#!/usr/bin/python3
"""STONE: prepare the GPU governor's configuration inside the image build.

STONE does not write its own values. It takes the configuration file shipped by the
cyan-skillfish-governor-smu package and changes exactly one thing: it turns on the
D-Bus interface stoned uses ([dbus] enabled = true). The voltage curve (safe-points),
frequency range and thermal thresholds stay as the package ships them.

The build fails, instead of producing an image with a broken performance chain, if:
  * the package has no configuration file or it is not valid TOML,
  * it has no [dbus] section (a governor version without the D-Bus interface),
  * after the change, dbus.enabled is not true or anything else differs,
  * the frequency range or thermal threshold are missing or outside the stable range.
"""
import re
import sys
import tomllib

PATH = sys.argv[1] if len(sys.argv) > 1 else "/etc/cyan-skillfish-governor-smu/config.toml"


def fail(msg):
    print(f"ERROR (governor config): {msg}", file=sys.stderr)
    sys.exit(1)


try:
    text = open(PATH).read()
    before = tomllib.loads(text)
except FileNotFoundError:
    fail(f"{PATH} not found; the package layout changed")
except tomllib.TOMLDecodeError as e:
    fail(f"{PATH} is not valid TOML: {e}")

if "dbus" not in before:
    fail("no [dbus] section: this governor version has no D-Bus interface for stoned")

if before["dbus"].get("enabled") is not True:
    # Replace only the 'enabled' line inside [dbus]; add it if the section lacks it.
    sec = re.search(r"(?ms)^\[dbus\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    body = sec.group(1)
    if re.search(r"(?m)^\s*enabled\s*=", body):
        body2 = re.sub(r"(?m)^(\s*)enabled\s*=.*$", r"\1enabled = true", body, count=1)
    else:
        body2 = "enabled = true\n" + body
    text = text[:sec.start(1)] + body2 + text[sec.end(1):]

after = tomllib.loads(text)
if after["dbus"].get("enabled") is not True:
    fail("could not enable [dbus]")
b2, a2 = dict(before), dict(after)
b2["dbus"] = {k: v for k, v in before["dbus"].items() if k != "enabled"}
a2["dbus"] = {k: v for k, v in after["dbus"].items() if k != "enabled"}
if a2 != b2:
    fail("the edit changed more than dbus.enabled")

rng = after.get("frequency-range", {})
mx, mn = rng.get("max"), rng.get("min")
if not isinstance(mx, int) or not isinstance(mn, int) or not (1000 <= mn <= mx <= 2000):
    fail(f"frequency-range {mn}-{mx} missing or outside 1000-2000 MHz")
temp = after.get("temperature", {}).get("throttling")
if not isinstance(temp, int) or not (65 <= temp <= 90):
    fail(f"temperature.throttling {temp} missing or outside 65-90 C")
if not after.get("safe-points"):
    fail("no safe-points (voltage curve) in the package configuration")

open(PATH, "w").write(text)
print(f"STONE_GOV_RANGE={mn}-{mx}")
print(f"STONE_GOV_THROTTLING={temp}")
print("STONE_GOV_SAFE_POINTS=" + ",".join(f"{p['frequency']}@{p['voltage']}" for p in after["safe-points"]))
