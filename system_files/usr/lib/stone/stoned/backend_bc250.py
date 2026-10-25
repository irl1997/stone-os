"""BC-250 Performance Backend.

The only module that knows about the GPU governor, hwmon and sysfs. If Bazzite or the
governor change, this is the only file that has to follow (audit section 13).

Hardware is never written directly. GPU frequency range, thermal threshold and load
target go through the D-Bus interface of cyan-skillfish-governor-smu. Those runtime
changes are not saved by the governor, so any restart of the governor or the machine
returns to its factory configuration file - the boot-safe baseline STONE relies on.
"""

import glob
import os
import re

from gi.repository import Gio, GLib

from .errors import StoneError, HARDWARE_ERROR, APPLY_FAILED, SERVICE_UNAVAILABLE, UNSUPPORTED, ROLLBACK_FAILED
from . import profiles as P

GOV_NAME = "com.cyanskillfish.Governor"
GOV_PATH = "/com/cyanskillfish/Governor"
GOV_PERF = "com.cyanskillfish.Governor.PerformanceMode"
GOV_RANGE = "com.cyanskillfish.Governor.Range"
RANGE_PATHS = {k: f"{GOV_PATH}/Range/{k.capitalize()}" for k in ("current", "allowed", "initial")}

# PCI device id of the Cyan Skillfish GPU on the BC-250. [VERIFICAR en placa con lspci -nn]
CYAN_SKILLFISH_IDS = {0x13FE}
AMD_VENDOR = 0x1002
TIMEOUT_MS = 2000

# BC-250 40 CU option (stone-cu40). The image's amdgpu has the option; it is in use when the
# kernel was started with amdgpu.bc250_cc_write_mode=2 or 3 (both write the unlock). With all
# 40 CU working the GPU draws ~30 W more and runs hotter at the same clock, so STONE then keeps
# it at 1500 MHz at most, whatever profile is chosen (the unlock's author measured 83 C there,
# 96 C at 2 GHz).
CU40_PARAM = "sys/module/amdgpu/parameters/bc250_cc_write_mode"
CU40_ACTIVE_MODES = {2, 3}
CU40_CEILING_MHZ = 1500


def _root():
    return os.environ.get("STONE_SYSFS_ROOT", "/")


def _p(*parts):
    return os.path.join(_root(), *[x.lstrip("/") for x in parts])


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path):
    v = _read(path)
    try:
        return int(v, 0) if v is not None else None
    except ValueError:
        return None


class Governor:
    """Thin, typed client for the governor's D-Bus API. Names of the Range properties
    are discovered by introspection (documentation shows both `Max` and `max`)."""

    def __init__(self, bus):
        self.bus = bus
        self._prop_names = {}

    def _call(self, dest, path, iface, method, args, reply):
        try:
            return self.bus.call_sync(dest, path, iface, method, args,
                                      GLib.VariantType(reply) if reply else None,
                                      Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        except GLib.Error as e:
            raise StoneError(HARDWARE_ERROR, f"{method}: {e.message}") from None

    def available(self):
        try:
            r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "NameHasOwner", GLib.Variant("(s)", (GOV_NAME,)), GLib.VariantType("(b)"),
                                   Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
            return bool(r.unpack()[0])
        except GLib.Error:
            return False

    def introspect(self, path):
        r = self._call(GOV_NAME, path, "org.freedesktop.DBus.Introspectable", "Introspect", None, "(s)")
        return Gio.DBusNodeInfo.new_for_xml(r.unpack()[0])

    def methods(self):
        node = self.introspect(GOV_PATH)
        iface = node.lookup_interface(GOV_PERF)
        return {m.name for m in iface.methods} if iface else set()

    def _range_props(self, path):
        if path not in self._prop_names:
            iface = self.introspect(path).lookup_interface(GOV_RANGE)
            if iface is None:
                raise StoneError(UNSUPPORTED, f"{path} has no {GOV_RANGE}")
            names = {p.name.lower(): p.name for p in iface.properties}
            if "min" not in names or "max" not in names:
                raise StoneError(UNSUPPORTED, f"{path} has no min/max properties")
            self._prop_names[path] = (names["min"], names["max"])
        return self._prop_names[path]

    def get(self, path, iface, prop):
        r = self._call(GOV_NAME, path, "org.freedesktop.DBus.Properties", "Get",
                       GLib.Variant("(ss)", (iface, prop)), "(v)")
        return r.unpack()[0]

    def read_range(self, which):
        path = RANGE_PATHS[which]
        mn, mx = self._range_props(path)
        return {"min": int(self.get(path, GOV_RANGE, mn)), "max": int(self.get(path, GOV_RANGE, mx))}

    def thresholds(self):
        return {"throttling": int(self.get(GOV_PATH, GOV_PERF, "TemperatureThrottling")),
                "recovery": int(self.get(GOV_PATH, GOV_PERF, "TemperatureRecovery"))}

    def load_target(self):
        return {"min": float(self.get(GOV_PATH, GOV_PERF, "LoadTargetMin")),
                "max": float(self.get(GOV_PATH, GOV_PERF, "LoadTargetMax"))}

    def set_range(self, mn, mx):
        self._call(GOV_NAME, GOV_PATH, GOV_PERF, "SetRange", GLib.Variant("(uu)", (mn, mx)), None)

    def set_thresholds(self, throttling, recovery):
        if throttling <= 0 or recovery <= 0 or recovery >= throttling:
            # 0,0 would disable the governor's thermal throttling. Never sent.
            raise StoneError(HARDWARE_ERROR, "refusing thresholds that weaken thermal protection")
        self._call(GOV_NAME, GOV_PATH, GOV_PERF, "SetTemperatureThresholds",
                   GLib.Variant("(uu)", (throttling, recovery)), None)

    def set_load_target(self, lo, hi):
        self._call(GOV_NAME, GOV_PATH, GOV_PERF, "SetLoadTarget", GLib.Variant("(dd)", (lo, hi)), None)


REQUIRED_METHODS = {"SetRange", "SetTemperatureThresholds", "SetLoadTarget"}


class BC250Backend:
    def __init__(self, bus):
        self.gov = Governor(bus)
        self.card = None
        self.caps = None

    # ---- detection -------------------------------------------------------------
    def _find_card(self):
        for dev in sorted(glob.glob(_p("sys/class/drm/card[0-9]*/device"))):
            vendor, device = _read_int(os.path.join(dev, "vendor")), _read_int(os.path.join(dev, "device"))
            driver = os.path.basename(os.path.realpath(os.path.join(dev, "driver"))) \
                if os.path.exists(os.path.join(dev, "driver")) else _read(os.path.join(dev, "driver_name"))
            if vendor == AMD_VENDOR and device in CYAN_SKILLFISH_IDS:
                return {"device": dev, "card": os.path.basename(os.path.dirname(dev)),
                        "driver": driver, "pci_id": f"{vendor:04x}:{device:04x}"}
        return None

    def _hwmon_by_name(self, *names):
        for h in sorted(glob.glob(_p("sys/class/hwmon/hwmon*"))):
            if _read(os.path.join(h, "name")) in names:
                return h
        return None

    def capabilities(self):
        """Detect hardware, driver, governor and sensors. Never assumes a BC-250."""
        self._paths = None
        caps = {"board": False, "driver": None, "governor": False, "gpu_range": (0, 0),
                "initial_range": None, "sensors": {}, "fan_control": False,
                "voltage_control": False, "cpu_control": False, "missing": []}
        card = self._find_card()
        if card:
            caps["board"] = True
            caps["driver"] = card["driver"]
            caps["card"] = card["card"]
            self.card = card
        else:
            caps["missing"].append("Cyan Skillfish GPU (BC-250)")
        if card and card["driver"] != "amdgpu":
            caps["missing"].append("amdgpu driver")
        if self.gov.available():
            try:
                missing = REQUIRED_METHODS - self.gov.methods()
                if missing:
                    caps["missing"].append("governor methods: " + ", ".join(sorted(missing)))
                else:
                    allowed = self.gov.read_range("allowed")
                    caps["gpu_range"] = (allowed["min"], allowed["max"])
                    caps["initial_range"] = self.gov.read_range("initial")
                    caps["governor"] = caps["board"] and card["driver"] == "amdgpu"
            except StoneError as e:
                caps["missing"].append(f"governor: {e.message}")
        else:
            caps["missing"].append("cyan-skillfish-governor-smu (D-Bus)")
        caps["sensors"] = {k: v is not None for k, v in self._sensor_paths().items()}
        caps["cu40"] = self.cu40()
        if caps["cu40"]["active"] and caps["governor"]:
            lo, hi = caps["gpu_range"]
            caps["gpu_range"] = (lo, min(hi, CU40_CEILING_MHZ))
        self.caps = caps
        return caps

    def cu40(self):
        """Whether the image's amdgpu has the 40 CU option and whether this boot uses it."""
        mode = _read_int(_p(CU40_PARAM))
        active = mode in CU40_ACTIVE_MODES
        return {"available": mode is not None, "active": active,
                "ceiling_mhz": CU40_CEILING_MHZ if active else None}

    def ceiling(self):
        return CU40_CEILING_MHZ if (self.caps or {}).get("cu40", {}).get("active") else None

    def _clamp(self, values):
        """With 40 CU in use nothing above the ceiling reaches the governor."""
        top = self.ceiling()
        if top is None:
            return values
        v = dict(values)
        v["max"] = min(v["max"], top)
        v["min"] = min(v["min"], v["max"])
        return v

    def enforce_ceiling(self):
        """Bring the governor under the ceiling if something (a governor restart, its factory
        file) put it above. Returns True if it had to act."""
        top = self.ceiling()
        if top is None or not self.gov.available():
            return False
        cur = self.gov.read_range("current")
        if cur["max"] <= top:
            return False
        self.gov.set_range(min(cur["min"], top), top)
        return True

    # ---- control ----------------------------------------------------------------
    def actual(self):
        """What the governor reports right now. ACTUAL STATE > REQUESTED STATE."""
        if not self.gov.available():
            raise StoneError(SERVICE_UNAVAILABLE, "GPU governor not running")
        cur = self.gov.read_range("current")
        th = self.gov.thresholds()
        return {"min": cur["min"], "max": cur["max"], "temp": th["throttling"], "recovery": th["recovery"]}

    def apply(self, values):
        """REQUEST -> EXECUTION -> VERIFY -> RETURN ACTUAL STATE. Rolls back on mismatch."""
        if not (self.caps and self.caps["governor"]):
            raise StoneError(UNSUPPORTED, "GPU control not available on this machine")
        before = self.actual()
        before["load"] = self.gov.load_target()
        calls = P.governor_calls(self._clamp(values))
        try:
            self.gov.set_range(*calls["range"])
            self.gov.set_thresholds(*calls["temperature"])
            self.gov.set_load_target(*calls["load_target"])
            after = self.actual()
        except StoneError:
            self._rollback(before)
            raise
        want = {"min": calls["range"][0], "max": calls["range"][1], "temp": calls["temperature"][0],
                "recovery": calls["temperature"][1]}
        load = self.gov.load_target()
        if any(after[k] != v for k, v in want.items()) or \
                abs(load["min"] - calls["load_target"][0]) > 1e-6 or abs(load["max"] - calls["load_target"][1]) > 1e-6:
            self._rollback(before)
            raise StoneError(APPLY_FAILED, f"governor reports {after}, requested {want}")
        return after

    def _rollback(self, before):
        try:
            self.gov.set_range(before["min"], before["max"])
            self.gov.set_thresholds(before["temp"], before["recovery"])
            if "load" in before:
                self.gov.set_load_target(before["load"]["min"], before["load"]["max"])
        except StoneError as e:
            raise StoneError(ROLLBACK_FAILED, e.message) from None

    def restore_initial(self):
        """Back to the governor's own factory values (what a reboot would give)."""
        init = self._clamp((self.caps or {}).get("initial_range") or self.gov.read_range("initial"))
        self.gov.set_range(init["min"], init["max"])
        self.gov.set_thresholds(P.FACTORY["balanced"]["temp"], P.FACTORY["balanced"]["temp"] - P.RECOVERY_GAP)
        up, lo = P.RESPONSE[1]
        self.gov.set_load_target(lo, up)
        return self.actual()

    # ---- telemetry ----------------------------------------------------------------
    def _sensor_paths(self):
        """Resolved once and cached; a read failure clears the cache so a sensor that
        appears later (a module loaded after boot) is found again."""
        if getattr(self, "_paths", None) is None:
            self._paths = self._find_sensor_paths()
        return self._paths

    def _find_sensor_paths(self):
        out = {"gpu_temp": None, "gpu_power": None, "gpu_clock": None, "gpu_metrics": None, "cpu_temp": None,
               "fan_rpm": None, "vram_used": None, "gtt_used": None}
        if self.card:
            dev = self.card["device"]
            hw = sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*")))
            if hw:
                t = os.path.join(hw[0], "temp1_input")
                out["gpu_temp"] = t if os.path.exists(t) else None
                for name in ("power1_average", "power1_input"):
                    if os.path.exists(os.path.join(hw[0], name)):
                        out["gpu_power"] = os.path.join(hw[0], name)
                        break
            for k, f in (("gpu_clock", "pp_dpm_sclk"), ("vram_used", "mem_info_vram_used"),
                         ("gtt_used", "mem_info_gtt_used"), ("gpu_metrics", "gpu_metrics")):
                if os.path.exists(os.path.join(dev, f)):
                    out[k] = os.path.join(dev, f)
        k10 = self._hwmon_by_name("k10temp")
        if k10 and os.path.exists(os.path.join(k10, "temp1_input")):
            out["cpu_temp"] = os.path.join(k10, "temp1_input")
        nct = self._hwmon_by_name("nct6686", "nct6683", "nct6687")
        if nct and os.path.exists(os.path.join(nct, "fan2_input")):
            out["fan_rpm"] = os.path.join(nct, "fan2_input")   # main fan header on the BC-250
        return out

    def _val(self, key, scale=1.0, digits=1):
        path = self._sensor_paths().get(key)
        if not path:
            return None
        v = _read_int(path)
        if v is None:
            self._paths = None
            return None
        return round(v / scale, digits) if scale != 1.0 else v

    def gpu_temp(self):
        v = self._val("gpu_temp", 1000.0, 0)
        return int(v) if v is not None else None

    def gpu_busy(self):
        """GPU activity from gpu_metrics (v2.x: average_gfx_activity at 0x1C, in 0.01 %).
        gpu_busy_percent does not work on the BC-250; the SMU governor patches this field
        (fix-metrics). 0xFFFF = not reported. [VERIFICAR en placa]"""
        path = self._sensor_paths().get("gpu_metrics")
        if not path:
            return None
        try:
            with open(path, "rb") as f:
                raw = f.read(64)
        except OSError:
            return None
        if len(raw) < 0x1E or raw[2] != 2:
            return None
        v = int.from_bytes(raw[0x1C:0x1E], "little")
        return None if v == 0xFFFF or v > 10000 else round(v / 100, 1)

    def gpu_clock(self):
        path = self._sensor_paths().get("gpu_clock")
        for line in ((_read(path) or "") if path else "").splitlines():
            mm = re.search(r"(\d+)\s*Mhz\s*\*", line, re.I)
            if mm:
                return int(mm.group(1))
        return None

    def metrics(self):
        """Real readings only; a missing sensor is None, never a guess."""
        m = {
            "gpu_temp_c": self._val("gpu_temp", 1000.0),
            "cpu_temp_c": self._val("cpu_temp", 1000.0),
            "gpu_power_w": self._val("gpu_power", 1e6),      # GPU chip only, not the console
            "fan_rpm": self._val("fan_rpm"),
            "gpu_clock_mhz": self.gpu_clock(),
            "gpu_busy_pct": self.gpu_busy(),
        }
        for k in ("vram_used", "gtt_used"):
            v = self._val(k)
            m[k + "_mb"] = round(v / 2**20) if v is not None else None
        return m
