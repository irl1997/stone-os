"""STONE Home -> stoned over D-Bus (the STONE PERFORMANCE API).

Only the methods listed in METHODS can be called, each with fixed argument types. The
UI cannot name a D-Bus method, a bus name, an object path or a file. Errors from stoned
arrive as org.stonemachine.Error.<Code> and are passed to the UI as that <Code>; a
missing or silent service is ServiceUnavailable.

Signals are accepted only from the bus name's current owner, so another process on the
bus cannot fake a state change.
"""

import os

from gi.repository import Gio, GLib

NAME = "org.stonemachine.Performance1"
PATH = "/org/stonemachine/Performance1"
IFACE = "org.stonemachine.Performance1"
ERR_PREFIX = "org.stonemachine.Error."
TIMEOUT_MS = 15000
KNOWN_ERRORS = {"Unsupported", "PermissionDenied", "InvalidValue", "HardwareError", "ServiceUnavailable",
                "ThermalLimit", "ApplyFailed", "RollbackComplete", "RollbackFailed", "SafeMode", "Busy",
                "NotValidated"}
SIGNALS = {"StateChanged": "state", "TestFinished": "testFinished", "Metrics": "metrics",
           "ThermalEvent": "thermal"}
PROFILE_IDS = {"silent", "balanced", "performance", "c1", "c2", "c3", "global", ""}
LEVELS = {"quick", "standard", "extended"}
CONTEXTS = {"performance_center", "overlay"}


class BridgeError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _profile(v):
    if not isinstance(v, str) or v not in PROFILE_IDS:
        raise BridgeError("InvalidValue", "profile")
    return v


def _appid(v):
    if not isinstance(v, str) or not v.isdigit() or not 0 < len(v) <= 10:
        raise BridgeError("InvalidValue", "appid")
    return v


def _int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise BridgeError("InvalidValue", "integer")
    return v


def _values(v):
    if not isinstance(v, dict) or set(v) - {"max", "min", "temp", "resp"} or not {"max", "min", "temp"} <= set(v):
        raise BridgeError("InvalidValue", "values")
    out = {"max": GLib.Variant("x", _int(v["max"], 0, 5000)), "min": GLib.Variant("x", _int(v["min"], 0, 5000)),
           "temp": GLib.Variant("x", _int(v["temp"], 0, 150))}
    if "resp" in v:
        out["resp"] = GLib.Variant("x", _int(v["resp"], 0, 9))
    return out


def _enum(allowed):
    def check(v):
        if v not in allowed:
            raise BridgeError("InvalidValue", "choice")
        return v
    return check


# js name -> (D-Bus method, [argument checkers], D-Bus input signature)
METHODS = {
    "getCapabilities": ("GetCapabilities", [], None),
    "getState": ("GetState", [], None),
    "getMetrics": ("GetMetrics", [], None),
    "homeReady": ("HomeReady", [], None),
    "setProfile": ("SetProfile", [_profile], "(s)"),
    "setSessionProfile": ("SetSessionProfile", [_profile], "(s)"),
    "startTest": ("StartTest", [lambda v: _int(v, 0, 2), _values, _enum(LEVELS)], "(ua{sv}s)"),
    "cancelTest": ("CancelTest", [], None),
    "measureBaseline": ("MeasureBaseline", [_enum(LEVELS)], "(s)"),
    "saveTestResult": ("SaveTestResult", [], None),
    "discardTestResult": ("DiscardTestResult", [], None),
    "restoreDefaults": ("RestoreDefaults", [], None),
    "enterSafeMode": ("EnterSafeMode", [], None),
    "exitSafeMode": ("ExitSafeMode", [], None),
    "setGameProfile": ("SetGameProfile", [_appid, _profile], "(ss)"),
    "subscribe": ("Subscribe", [_enum(CONTEXTS)], "(s)"),
    "unsubscribe": ("Unsubscribe", [], None),
}


def error_code(gerror):
    remote = Gio.DBusError.get_remote_error(gerror) if Gio.DBusError.is_remote_error(gerror) else None
    if remote and remote.startswith(ERR_PREFIX):
        code = remote[len(ERR_PREFIX):]
        return code if code in KNOWN_ERRORS else "HardwareError"
    if remote in ("org.freedesktop.DBus.Error.AccessDenied",):
        return "PermissionDenied"
    return "ServiceUnavailable"


class Stoned:
    def __init__(self, on_event, bus=None):
        self.on_event = on_event
        if bus is None:
            bus_type = Gio.BusType.SESSION if os.environ.get("STONE_BUS") == "session" else Gio.BusType.SYSTEM
            bus = Gio.bus_get_sync(bus_type, None)
        self.bus = bus
        self.owner = None
        self.bus.signal_subscribe(None, IFACE, None, PATH, None, Gio.DBusSignalFlags.NONE, self._signal)
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", NAME, Gio.DBusSignalFlags.NONE, self._owner_changed)
        try:
            r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "GetNameOwner", GLib.Variant("(s)", (NAME,)), GLib.VariantType("(s)"),
                                   Gio.DBusCallFlags.NONE, 2000, None)
            self.owner = r.unpack()[0]
        except GLib.Error:
            self.owner = None

    def _owner_changed(self, conn, sender, path, iface, signal, params):
        name, _old, new = params.unpack()
        if name != NAME:
            return
        self.owner = new or None
        self.on_event("service", {"up": bool(new)})

    def _signal(self, conn, sender, path, iface, signal, params):
        if self.owner is None or sender != self.owner or signal not in SIGNALS:
            return
        try:
            data = params.unpack()[0]
        except (IndexError, TypeError):
            return
        self.on_event(SIGNALS[signal], data)

    def call(self, method, args, done):
        """done(result_dict) or done(None, error_code, message). Never raises for bad input."""
        spec = METHODS.get(method)
        if spec is None:
            return done(None, "InvalidValue", "unknown method")
        dbus_method, checks, sig = spec
        if not isinstance(args, list) or len(args) != len(checks):
            return done(None, "InvalidValue", "wrong number of arguments")
        try:
            checked = [c(a) for c, a in zip(checks, args, strict=True)]
        except BridgeError as e:
            return done(None, e.code, e.message)
        params = GLib.Variant(sig, tuple(checked)) if sig else None

        def finished(conn, res):
            try:
                out = conn.call_finish(res)
            except GLib.Error as e:
                return done(None, error_code(e), e.message)
            done(out.unpack()[0])

        self.bus.call(NAME, PATH, IFACE, dbus_method, params, GLib.VariantType("(a{sv})"),
                      Gio.DBusCallFlags.NONE, TIMEOUT_MS, None, finished)
