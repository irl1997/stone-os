"""Fake cyan-skillfish-governor-smu D-Bus service for tests (session bus).

Implements the interface documented in the governor README: PerformanceMode methods and
properties on /com/cyanskillfish/Governor and the Range objects. It is a test double,
not a reference: names and behaviour must be re-checked against the real governor.

Fault injection through FAKE_GOV_MODE:
  normal       behaves like the documented governor
  ignore       accepts SetRange but keeps the old range (-> stoned must report ApplyFailed)
  error        every Set* call returns a D-Bus error
Property name case through FAKE_GOV_CASE: "lower" (min/max) or "title" (Min/Max).
Every call is appended to FAKE_GOV_LOG as JSON lines, so tests can check what was sent.
"""

import json
import os

from gi.repository import Gio, GLib

MODE = os.environ.get("FAKE_GOV_MODE", "normal")
CASE = os.environ.get("FAKE_GOV_CASE", "lower")
LOG = os.environ.get("FAKE_GOV_LOG", "/dev/null")
MN, MX = ("min", "max") if CASE == "lower" else ("Min", "Max")

ALLOWED = (1000, 2000)          # shipped curve without the kernel patch
INITIAL = [1000, 1850]          # shipped config.toml
state = {"current": list(INITIAL), "throttling": 85, "recovery": 75, "load": [0.50, 0.65], "enabled": False}

XML_MAIN = """<node>
 <interface name="com.cyanskillfish.Governor.PerformanceMode">
  <method name="SetFixedFrequency"><arg type="u" direction="in"/></method>
  <method name="SetRange"><arg type="u" direction="in"/><arg type="u" direction="in"/></method>
  <method name="SetLoadTarget"><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
  <method name="SetTemperatureThresholds"><arg type="u" direction="in"/><arg type="u" direction="in"/></method>
  <property name="Enabled" type="b" access="readwrite"/>
  <property name="LoadTargetMin" type="d" access="readwrite"/>
  <property name="LoadTargetMax" type="d" access="readwrite"/>
  <property name="TemperatureThrottling" type="u" access="readwrite"/>
  <property name="TemperatureRecovery" type="u" access="readwrite"/>
 </interface>
</node>"""
XML_RANGE = f"""<node><interface name="com.cyanskillfish.Governor.Range">
  <property name="{MN}" type="u" access="read"/><property name="{MX}" type="u" access="read"/>
</interface></node>"""


def log(entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main_call(conn, sender, path, iface, method, params, inv):
    args = params.unpack()
    log({"call": method, "args": list(args)})
    if MODE == "error":
        return inv.return_dbus_error("com.cyanskillfish.Governor.Error", "injected failure")
    if method == "SetRange":
        mn, mx = args
        mn = mn or ALLOWED[0]
        mx = mx or ALLOWED[1]
        if mn < ALLOWED[0] or mx > ALLOWED[1] or mn > mx:
            return inv.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", "outside safe points")
        if MODE != "ignore":
            state["current"] = [mn, mx]
    elif method == "SetTemperatureThresholds":
        state["throttling"], state["recovery"] = args     # 0,0 disables throttling in the real one
    elif method == "SetLoadTarget":
        state["load"] = list(args)
    elif method == "SetFixedFrequency":
        state["enabled"] = True
    inv.return_value(None)


def main_get(conn, sender, path, iface, prop):
    return {"Enabled": GLib.Variant("b", state["enabled"]),
            "LoadTargetMin": GLib.Variant("d", state["load"][0]),
            "LoadTargetMax": GLib.Variant("d", state["load"][1]),
            "TemperatureThrottling": GLib.Variant("u", state["throttling"]),
            "TemperatureRecovery": GLib.Variant("u", state["recovery"])}[prop]


def range_get(which):
    def get(conn, sender, path, iface, prop):
        vals = {"current": state["current"], "allowed": list(ALLOWED), "initial": INITIAL}[which]
        return GLib.Variant("u", vals[0] if prop == MN else vals[1])
    return get


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.register_object("/com/cyanskillfish/Governor", Gio.DBusNodeInfo.new_for_xml(XML_MAIN).interfaces[0],
                        main_call, main_get, None)
    rng = Gio.DBusNodeInfo.new_for_xml(XML_RANGE).interfaces[0]
    for which in ("current", "allowed", "initial"):
        bus.register_object(f"/com/cyanskillfish/Governor/Range/{which.capitalize()}", rng, None, range_get(which), None)
    Gio.bus_own_name_on_connection(bus, "com.cyanskillfish.Governor", Gio.BusNameOwnerFlags.NONE, None, None)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
