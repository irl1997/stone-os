"""STONE PERFORMANCE as the optimizer sees it: stoned's state, capabilities and per-game profile.

The optimizer never touches the GPU: it asks stoned for a per-game profile (silent, balanced,
performance; stoned refuses anything its calibration did not validate) and stoned applies it
when Steam starts the game and goes back to the global profile when it ends. stoned's own
recovery (Last Known Good -> BALANCED) stays in charge of performance crashes.
"""

import threading

TIMEOUT_S = 10


class PerfAsync:
    """Over STONE Home's bridge (call(method, args, done) answered on the main loop). Used from
    a worker thread only."""

    def __init__(self, stoned):
        self.stoned = stoned

    def _call(self, method, args):
        ev, box = threading.Event(), {}

        def done(res, code=None, msg=""):
            box["r"], box["e"] = res, (code, msg) if res is None else None
            ev.set()
        self.stoned.call(method, args, done)
        if not ev.wait(TIMEOUT_S):
            return None, "stoned did not answer"
        return box["r"], (f"{box['e'][0]}: {box['e'][1]}" if box.get("e") else None)

    def state(self):
        return self._call("getState", [])[0]

    def capabilities(self):
        return self._call("getCapabilities", [])[0]

    def set_game_profile(self, appid, pid):
        r, err = self._call("setGameProfile", [str(appid), pid])
        return r is not None, err


class PerfDBus:
    """Direct D-Bus (the stone-game command)."""

    NAME, PATH, IFACE = "org.stonemachine.Performance1", "/org/stonemachine/Performance1", "org.stonemachine.Performance1"

    def __init__(self):
        from gi.repository import Gio
        self.Gio = Gio
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:  # noqa: BLE001
            self.bus = None

    def _call(self, method, sig=None, args=()):
        from gi.repository import GLib
        if self.bus is None:
            return None, "no system bus"
        try:
            r = self.bus.call_sync(self.NAME, self.PATH, self.IFACE, method, GLib.Variant(sig, args) if sig else None,
                                   GLib.VariantType("(a{sv})"), self.Gio.DBusCallFlags.NONE, TIMEOUT_S * 1000, None)
            return r.unpack()[0], None
        except GLib.Error as e:
            return None, e.message

    def state(self):
        return self._call("GetState")[0]

    def capabilities(self):
        return self._call("GetCapabilities")[0]

    def set_game_profile(self, appid, pid):
        r, err = self._call("SetGameProfile", "(ss)", (str(appid), pid))
        return r is not None, err
