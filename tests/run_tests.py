"""stoned test suite: recovery logic, then integration against the fake governor.

Each integration test gets its own private D-Bus daemon, fake sysfs tree, state
directory and fake journalctl, then talks to a real `stoned` process over D-Bus.
Nothing here touches real hardware. Passing means CODE TESTED, never HARDWARE VERIFIED.

    python3 tests/run_tests.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIB = os.path.join(REPO, "system_files", "usr", "lib", "stone")
sys.path.insert(0, LIB)

from gi.repository import Gio, GLib  # noqa: E402
from stoned import state as S, recover as R  # noqa: E402

PY = sys.executable
RESULTS = []


def test(fn):
    RESULTS.append(fn)
    return fn


# ---------------------------------------------------------------- fixtures
def write_gpu_metrics(root, activity):
    raw = bytearray(64)
    raw[0:2] = (64).to_bytes(2, "little"); raw[2] = 2; raw[3] = 0     # header, format v2
    raw[0x1C:0x1E] = activity.to_bytes(2, "little")                    # average_gfx_activity
    with open(os.path.join(root, "sys/class/drm/card1/device/gpu_metrics"), "wb") as f:
        f.write(bytes(raw))


def write_proc_stat(root, busy, idle):
    with open(os.path.join(root, "proc/stat"), "w") as f:
        f.write(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\n")


def make_sysfs(root, board=True, gpu_temp_c=55, gpu_errors=False, fan=True):
    dev = os.path.join(root, "sys/class/drm/card1/device")
    os.makedirs(os.path.join(dev, "hwmon/hwmon3"))
    os.makedirs(os.path.join(root, "sys/bus/pci/drivers/amdgpu"))
    os.symlink(os.path.join(root, "sys/bus/pci/drivers/amdgpu"), os.path.join(dev, "driver"))
    w = lambda p, v: open(os.path.join(root, p), "w").write(str(v) + "\n")
    w("sys/class/drm/card1/device/vendor", "0x1002")
    w("sys/class/drm/card1/device/device", "0x13fe" if board else "0x73bf")
    w("sys/class/drm/card1/device/pp_dpm_sclk", "0: 1000Mhz\n1: 1500Mhz *\n2: 2000Mhz")
    w("sys/class/drm/card1/device/mem_info_vram_used", 300 * 2**20)
    w("sys/class/drm/card1/device/mem_info_gtt_used", 2048 * 2**20)
    w("sys/class/drm/card1/device/hwmon/hwmon3/name", "amdgpu")
    w("sys/class/drm/card1/device/hwmon/hwmon3/temp1_input", gpu_temp_c * 1000)
    w("sys/class/drm/card1/device/hwmon/hwmon3/power1_average", 61_000_000)
    write_gpu_metrics(root, 4250)
    os.makedirs(os.path.join(root, "proc"))
    write_proc_stat(root, 1000, 9000)
    w("proc/meminfo", "MemTotal:       16000000 kB\nMemFree:  1 kB\nMemAvailable:   12000000 kB")
    for n, name, f, v in ((4, "k10temp", "temp1_input", 48000), (5, "nct6686", "fan2_input", 1450)):
        if name == "nct6686" and not fan:
            continue
        d = os.path.join(root, f"sys/class/hwmon/hwmon{n}")
        os.makedirs(d)
        w(f"sys/class/hwmon/hwmon{n}/name", name)
        w(f"sys/class/hwmon/hwmon{n}/{f}", v)


def set_gpu_clock(root, mhz):
    with open(os.path.join(root, "sys/class/drm/card1/device/pp_dpm_sclk"), "w") as f:
        f.write(f"0: 1000Mhz\n1: {mhz}Mhz *\n")


def set_fan(root, rpm):
    with open(os.path.join(root, "sys/class/hwmon/hwmon5/fan2_input"), "w") as f:
        f.write(f"{rpm}\n")


def set_gpu_temp(root, c):
    with open(os.path.join(root, "sys/class/drm/card1/device/hwmon/hwmon3/temp1_input"), "w") as f:
        f.write(f"{c * 1000}\n")


class Env:
    def __init__(self, board=True, gov_mode="normal", gov_case="lower", gov=True, polkit_off=True,
                 gpu_errors=False, test_seconds=3, workload=("sleep", "3600"), fan=True, thermal_scale=None,
                 game_source="dbus", min_session_s=None, cu40=None):
        self.tmp = tempfile.mkdtemp(prefix="stoned-test-")
        self.sysfs = os.path.join(self.tmp, "root")
        self.state_dir = os.path.join(self.tmp, "state")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        make_sysfs(self.sysfs, board=board, fan=fan)
        if cu40 is not None:     # the image's amdgpu has the 40 CU option; value = this boot's mode
            os.makedirs(os.path.join(self.sysfs, "sys/module/amdgpu/parameters"))
            with open(os.path.join(self.sysfs, "sys/module/amdgpu/parameters/bc250_cc_write_mode"), "w") as f:
                f.write(f"{cu40}\n")
        # fake journalctl: prints an amdgpu error only when asked to
        with open(os.path.join(self.bin, "journalctl"), "w") as f:
            f.write("#!/bin/sh\n" + ("echo 'amdgpu 0000:01:00.0: amdgpu: ring gfx timeout, signaled seq=1'\n" if gpu_errors else "") + "exit 0\n")
        os.chmod(os.path.join(self.bin, "journalctl"), 0o755)
        with open(os.path.join(self.tmp, "stability.json"), "w") as f:
            json.dump({"command": list(workload)}, f)
        self.gov_log = os.path.join(self.tmp, "gov.log")
        self.daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                                       stdout=subprocess.PIPE, text=True)
        self.address = self.daemon.stdout.readline().strip()
        self.env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.address, STONE_BUS="session",
                        STONE_SYSFS_ROOT=self.sysfs, STONE_STATE_DIR=self.state_dir,
                        STONE_STABILITY_CONF=os.path.join(self.tmp, "stability.json"),
                        STONE_TEST_SECONDS=str(test_seconds), PATH=self.bin + ":" + os.environ["PATH"],
                        FAKE_GOV_MODE=gov_mode, FAKE_GOV_CASE=gov_case, FAKE_GOV_LOG=self.gov_log,
                        PYTHONPATH=LIB, STONE_CTL_ALLOW_USER="1")
        self.cmdline = os.path.join(self.tmp, "cmdline")
        with open(self.cmdline, "w") as f:
            f.write("quiet splash\n")
        self.env["STONE_CMDLINE"] = self.cmdline
        if thermal_scale:
            self.env["STONE_THERMAL_SCALE"] = str(thermal_scale)
        self.env["STONE_GAME_SOURCE"] = game_source
        if min_session_s is not None:
            self.env["STONE_MIN_SESSION_S"] = str(min_session_s)
        if polkit_off:
            self.env["STONE_POLKIT"] = "0"
        else:
            self.env.pop("STONE_POLKIT", None)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            self.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.gov = None
        self.stoned = None
        if gov:
            self.start_governor()

    def start_governor(self):
        self.gov = subprocess.Popen([PY, os.path.join(HERE, "fake_governor.py")], env=self.env)
        self.wait_name("com.cyanskillfish.Governor")

    def stop_governor(self):
        if self.gov:
            self.gov.terminate()
            self.gov.wait()
            self.gov = None
            self.wait_name("com.cyanskillfish.Governor", present=False)

    def recover(self, cmdline=None):
        args = [PY, "-m", "stoned", "--recover"]
        out = subprocess.run(args, env=self.env, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def start_stoned(self):
        self.stoned = subprocess.Popen([PY, "-m", "stoned"], env=self.env, stderr=subprocess.PIPE, text=True)
        self.wait_name("org.stonemachine.Performance1")

    def kill_stoned(self, sig=signal.SIGKILL):
        self.stoned.send_signal(sig)
        self.stoned.wait()
        self.wait_name("org.stonemachine.Performance1", present=False)

    def wait_name(self, name, present=True, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "NameHasOwner", GLib.Variant("(s)", (name,)), None, 0, 2000, None)
            if bool(r.unpack()[0]) == present:
                return
            time.sleep(0.05)
        raise AssertionError(f"name {name} present={present} timed out")

    def call(self, method, sig=None, *args):
        try:
            r = self.bus.call_sync("org.stonemachine.Performance1", "/org/stonemachine/Performance1",
                                   "org.stonemachine.Performance1", method,
                                   GLib.Variant(sig, args) if sig else None, None, 0, 10000, None)
            return r.unpack()[0]
        except GLib.Error as e:
            return {"__error__": Gio.DBusError.get_remote_error(e)}

    def gov_calls(self):
        try:
            return [json.loads(x) for x in open(self.gov_log)]
        except FileNotFoundError:
            return []

    def gov_state(self):
        def get(path, iface, prop):
            r = self.bus.call_sync("com.cyanskillfish.Governor", path, "org.freedesktop.DBus.Properties", "Get",
                                   GLib.Variant("(ss)", (iface, prop)), None, 0, 2000, None)
            return r.unpack()[0]
        mn, mx = ("min", "max") if self.env["FAKE_GOV_CASE"] == "lower" else ("Min", "Max")
        p = "/com/cyanskillfish/Governor/Range/Current"
        return {"min": get(p, "com.cyanskillfish.Governor.Range", mn), "max": get(p, "com.cyanskillfish.Governor.Range", mx),
                "temp": get("/com/cyanskillfish/Governor", "com.cyanskillfish.Governor.PerformanceMode", "TemperatureThrottling"),
                "recovery": get("/com/cyanskillfish/Governor", "com.cyanskillfish.Governor.PerformanceMode", "TemperatureRecovery")}

    def store(self):
        return S.Store(self.state_dir)

    def close(self):
        for p in (self.stoned, self.gov):
            if p and p.poll() is None:
                p.kill()
                p.wait()
        self.daemon.kill()
        self.daemon.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)


def err(r):
    return r.get("__error__", "") if isinstance(r, dict) else ""


def wait_test_done(env, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = env.call("GetState")
        if "testing" not in st:
            return st
        time.sleep(0.3)
    raise AssertionError("test did not finish")


def V(d):
    return {k: GLib.Variant("u", v) for k, v in d.items()}


# ---------------------------------------------------------------- recovery (P0-1, P0-2)
def rstore():
    d = tempfile.mkdtemp(prefix="stone-rec-")
    return S.Store(d), d


def cmdline(text):
    f = tempfile.NamedTemporaryFile("w", delete=False)
    f.write(text)
    f.close()
    return f.name


NORMAL = None


@test
def R01_first_boot_is_clean():
    st, _ = rstore()
    r = R.recover(st, cmdline("quiet"))
    assert r["clean"] and r["fail_count"] == 0 and not r["safe_mode"] and r["profile"] == "balanced", r
    assert st.get_flag(S.FLAG_BOOT) is not None


@test
def R02_clean_shutdown_resets_counter():
    st, _ = rstore()
    s = st.load(); s["fail_count"] = 1; st.save(s)
    R.mark_clean_shutdown(st)
    r = R.recover(st, cmdline("quiet"))
    assert r["fail_count"] == 0 and r["clean"], r


@test
def R03_crash_during_test_falls_back_to_last_known_good():
    st, _ = rstore()
    s = st.load(); s["profile"] = "performance"; s["last_known_good"] = "performance"; st.save(s)
    st.set_flag(S.FLAG_TRIAL, {"profile": "c1", "values": {"max": 2000}})
    r = R.recover(st, cmdline("quiet"))
    assert r["fail_count"] == 1 and not r["safe_mode"] and r["profile"] == "performance", r
    assert st.get_flag(S.FLAG_TRIAL) is None and st.load()["slots"][0] is None
    assert "stability test discarded" in r["actions"]


@test
def R04_two_failures_in_a_row_enter_safe_mode():
    st, _ = rstore()
    st.save(st.load())
    for _ in range(2):
        st.set_flag(S.FLAG_TRIAL, {"profile": "c1"})
        r = R.recover(st, cmdline("quiet"))
    assert r["fail_count"] == 2 and r["safe_mode"], r


@test
def R05_crash_with_unvalidated_slot_marks_it_failed():
    st, _ = rstore()
    s = st.load()
    s["slots"][1] = {"max": 1950, "min": 1000, "temp": 80, "resp": 1, "state": "tested", "test_level": "standard"}
    s["profile"] = "c2"
    st.save(s)
    st.set_flag(S.FLAG_PROVISIONAL, {"profile": "c2"})
    r = R.recover(st, cmdline("quiet"))
    s = st.load()
    assert s["slots"][1]["state"] == "failed" and s["profile"] == "balanced" and r["fail_count"] == 1, (r, s)


@test
def R06_kernel_parameter_forces_safe_mode():
    st, _ = rstore()
    r = R.recover(st, cmdline("quiet stone.safe=1 splash"))
    assert r["safe_mode"], r


@test
def R07_unvalidated_profile_never_survives_a_boot():
    st, _ = rstore()
    s = st.load()
    s["slots"][0] = {"max": 2000, "min": 1000, "temp": 85, "resp": 1, "state": "tested", "test_level": "quick"}
    s["profile"] = "c1"
    st.save(s)
    R.mark_clean_shutdown(st)
    r = R.recover(st, cmdline("quiet"))
    assert r["profile"] == "balanced" and st.load()["slots"][0]["state"] == "tested", r


@test
def R08_corrupt_state_starts_from_factory():
    st, d = rstore()
    with open(os.path.join(d, "state.json"), "w") as f:
        f.write("{not json")
    r = R.recover(st, cmdline("quiet"))
    assert r["profile"] == "balanced" and os.path.exists(os.path.join(d, "state.json.corrupt")), r


@test
def R09_crash_at_factory_profile_after_home_is_not_counted():
    st, _ = rstore()
    st.save(st.load())
    r = R.recover(st, cmdline("quiet"))
    st.clear_flag(S.FLAG_BOOT)          # Home stayed up 60 s
    r = R.recover(st, cmdline("quiet"))  # then a game hung the GPU at BALANCED
    assert r["fail_count"] == 0 and st.load()["last_failure"]["during"] == "session", r


@test
def R10_boot_loop_before_home_enters_safe_mode():
    st, _ = rstore()
    st.save(st.load())
    R.recover(st, cmdline("quiet"))      # boot flag set, Home never comes up
    R.recover(st, cmdline("quiet"))
    r = R.recover(st, cmdline("quiet"))
    assert r["safe_mode"] and r["fail_count"] >= 2, r


@test
def R11_crash_with_validated_custom_counts_and_falls_back():
    st, _ = rstore()
    s = st.load()
    s["slots"][0] = {"max": 2000, "min": 1000, "temp": 85, "resp": 1, "state": "validated", "test_level": "standard"}
    s["profile"] = s["last_known_good"] = "c1"
    st.save(s)
    r = R.recover(st, cmdline("quiet"))
    st.clear_flag(S.FLAG_BOOT)
    r = R.recover(st, cmdline("quiet"))
    s = st.load()
    assert r["fail_count"] == 1 and s["profile"] == "balanced" and s["slots"][0]["state"] == "validated", (r, s)


@test
def R12_malformed_slots_are_dropped():
    st, d = rstore()
    with open(os.path.join(d, "state.json"), "w") as f:
        json.dump({"schema": 1, "profile": "c1", "slots": [{"max": "x"}, {"max": 2000, "min": 1000, "temp": 85, "state": "root"}], "per_game": {"../x": "c1", "570": "silent"}}, f)
    s = st.load()
    assert s["slots"] == [None, None, None] and s["profile"] == "balanced" and s["per_game"] == {"570": "silent"}, s


# ---------------------------------------------------------------- integration
def integration(**kw):
    def deco(fn):
        def run():
            env = Env(**kw)
            try:
                env.recover()
                env.start_stoned()
                fn(env)
            finally:
                env.close()
        run.__name__ = fn.__name__
        RESULTS.append(run)
        return run
    return deco


@integration()
def I01_capabilities_come_from_the_governor(env):
    c = env.call("GetCapabilities")
    assert c["board"] and c["governor"] and c["advanced"] and c["gpu_range"] == [1000, 2000], c
    assert c["max_range"] == [1500, 2000] and c["min_range"] == [1000, 1500], c
    assert c["sensors"]["gpu_temp"] and c["sensors"]["fan_rpm"], c


@integration(cu40=3)
def I26_with_40_cu_the_gpu_never_goes_above_1500_mhz(env):
    c = env.call("GetCapabilities")
    assert c["cu40"] == {"available": True, "active": True, "ceiling_mhz": 1500}, c
    assert c["gpu_range"] == [1000, 1500] and c["max_range"] == [1500, 1500], c
    # the governor started from its factory file (1850): stoned brought it down at start
    assert env.gov_state()["max"] == 1500, env.gov_state()
    for pid in ("performance", "balanced"):
        r = env.call("SetProfile", "(s)", pid)
        assert r["actual"]["max"] == 1500 and r["profile"] == pid, r
    assert env.gov_state()["max"] == 1500, env.gov_state()
    r = env.call("RestoreDefaults")
    assert r["actual"]["max"] == 1500, r
    bad = env.call("StartTest", "(ua{sv}s)", 0, V({"max": 1700, "min": 1000, "temp": 80, "resp": 1}), "quick")
    assert "InvalidValue" in err(bad), bad


@integration(cu40=0)
def I27_40_cu_option_present_but_off_changes_nothing(env):
    c = env.call("GetCapabilities")
    assert c["cu40"] == {"available": True, "active": False}, c
    assert c["gpu_range"] == [1000, 2000], c
    r = env.call("SetProfile", "(s)", "performance")
    assert r["actual"]["max"] == 2000, r


@integration(board=False)
def I02_not_a_bc250_disables_everything(env):
    c = env.call("GetCapabilities")
    assert not c["board"] and not c["governor"] and not c["advanced"], c
    assert "Unsupported" in err(env.call("SetProfile", "(s)", "performance"))
    assert env.gov_calls() == [], env.gov_calls()


@integration(gov_case="title")
def I03_set_profile_reads_back_actual_state(env):
    r = env.call("SetProfile", "(s)", "performance")
    assert r["actual"] == {"min": 1000, "max": 2000, "temp": 85, "recovery": 75}, r
    assert env.gov_state() == {"min": 1000, "max": 2000, "temp": 85, "recovery": 75}
    r = env.call("SetProfile", "(s)", "silent")
    assert r["actual"]["max"] == 1500 and r["actual"]["temp"] == 75 and r["actual"]["recovery"] == 65, r
    assert r["profile"] == "silent" and r["last_known_good"] == "silent", r


@integration(gov_mode="ignore")
def I04_governor_ignoring_a_request_is_reported_not_hidden(env):
    r = env.call("SetProfile", "(s)", "performance")
    assert "ApplyFailed" in err(r), r
    st = env.call("GetState")
    assert st["actual"]["max"] == 1850 and st["profile"] == "balanced", st


@integration()
def I05_invalid_values_are_rejected_by_the_service(env):
    base = {"max": 1950, "min": 1000, "temp": 80, "resp": 1}
    for bad in ({**base, "max": 2100}, {**base, "max": 1400}, {**base, "min": 1600}, {**base, "temp": 90},
                {**base, "temp": 0}, {**base, "max": 1975}, {"max": 2000}, {**base, "volt": 1100}):
        r = env.call("StartTest", "(ua{sv}s)", 0, V(bad), "quick")
        assert "InvalidValue" in err(r), (bad, r)
    r = env.call("StartTest", "(ua{sv}s)", 0, {**V(base), "temp": GLib.Variant("b", True)}, "quick")
    assert "InvalidValue" in err(r), r
    assert "InvalidValue" in err(env.call("StartTest", "(ua{sv}s)", 7, V(base), "quick"))
    assert "InvalidValue" in err(env.call("StartTest", "(ua{sv}s)", 0, V(base), "forever"))
    assert "InvalidValue" in err(env.call("SetProfile", "(s)", "../../etc/passwd"))
    assert "InvalidValue" in err(env.call("SetGameProfile", "(ss)", "570; rm -rf /", "silent"))
    assert all(c["call"] != "SetTemperatureThresholds" or c["args"][0] > 0 for c in env.gov_calls())
    assert [c for c in env.gov_calls() if c["call"] == "SetRange"] == [], env.gov_calls()


@integration()
def I06_test_applies_temporarily_and_never_activates_the_slot(env):
    env.call("SetProfile", "(s)", "silent")
    r = env.call("StartTest", "(ua{sv}s)", 0, V({"max": 1950, "min": 1000, "temp": 80, "resp": 1}), "quick")
    assert r["testing"]["slot"] == 0, r
    assert env.store().get_flag(S.FLAG_TRIAL)["profile"] == "c1"
    assert env.gov_state()["max"] == 1950
    st = wait_test_done(env)
    assert env.store().get_flag(S.FLAG_TRIAL) is None
    assert st["slots"][0]["state"] == "empty" and st["pending_result"]["slot"] == 0, st   # nothing saved yet
    st = env.call("SaveTestResult")
    assert st["slots"][0]["state"] == "tested" and st["slots"][0]["test_level"] == "quick", st
    assert st["profile"] == "silent" and env.gov_state()["max"] == 1500, st      # P0-3
    assert st["tests"][-1]["passed"] is True, st


@integration(test_seconds=30)
def I07_service_killed_mid_test_restores_last_known_good(env):
    env.call("SetProfile", "(s)", "silent")
    env.call("StartTest", "(ua{sv}s)", 1, V({"max": 2000, "min": 1000, "temp": 85, "resp": 2}), "quick")
    assert env.gov_state()["max"] == 2000
    env.kill_stoned()
    env.start_stoned()
    st = env.call("GetState")
    assert env.gov_state()["max"] == 1500 and st["notice"] == "RollbackComplete", st
    assert env.store().get_flag(S.FLAG_TRIAL) is None and st["slots"][1]["state"] == "empty", st


@integration()
def I08_slot_is_validated_only_after_standard_test_and_clean_session(env):
    vals = V({"max": 1950, "min": 1000, "temp": 80, "resp": 1})
    env.call("StartTest", "(ua{sv}s)", 0, vals, "quick")
    wait_test_done(env)
    env.call("SaveTestResult")
    r = env.call("SetProfile", "(s)", "c1")
    assert r["provisional"] and r["last_known_good"] == "balanced", r
    env.call("GameStarted", "(s)", "570")
    r = env.call("GameEnded", "(sb)", "570", True)
    assert r["slots"][0]["state"] == "tested", r                # quick only: not enough
    env.call("SetProfile", "(s)", "balanced")
    set_gpu_clock(env.sysfs, 1940)                              # STANDARD needs the clock sustained
    env.call("StartTest", "(ua{sv}s)", 0, vals, "standard")
    wait_test_done(env)
    env.call("SaveTestResult")
    env.call("SetProfile", "(s)", "c1")
    env.call("GameStarted", "(s)", "570")
    r = env.call("GameEnded", "(sb)", "570", False)             # game crashed: not validated
    assert r["slots"][0]["state"] == "tested", r
    env.call("GameStarted", "(s)", "570")
    r = env.call("GameEnded", "(sb)", "570", True)
    assert r["slots"][0]["state"] == "validated" and r["last_known_good"] == "c1" and not r["provisional"], r


@integration()
def I09_safe_mode_allows_nothing_above_balanced(env):
    env.kill_stoned()
    s = env.store().load(); s["safe_mode"] = True; env.store().save(s)
    env.start_stoned()
    assert "SafeMode" in err(env.call("SetProfile", "(s)", "performance"))
    assert env.call("SetProfile", "(s)", "silent")["actual"]["max"] == 1500
    r = env.call("StartTest", "(ua{sv}s)", 0, V({"max": 1800, "min": 1000, "temp": 80, "resp": 1}), "quick")
    assert "Unsupported" in err(r) or "SafeMode" in err(r), r


@integration()
def I10_hot_gpu_refuses_a_higher_ceiling_but_allows_a_lower_one(env):
    env.call("SetProfile", "(s)", "silent")
    set_gpu_temp(env.sysfs, 88)
    assert "ThermalLimit" in err(env.call("SetProfile", "(s)", "performance"))
    assert env.call("SetProfile", "(s)", "silent")["actual"]["max"] == 1500


@integration()
def I11_restore_defaults_clears_per_game_and_returns_to_factory(env):
    env.call("SetProfile", "(s)", "performance")
    env.call("SetGameProfile", "(ss)", "570", "silent")
    r = env.call("RestoreDefaults")
    assert r["profile"] == "balanced" and r["per_game"] == {} and r["actual"]["max"] == 1850, r


@integration(gpu_errors=True)
def I12_gpu_errors_in_kernel_log_fail_the_test(env):
    env.call("StartTest", "(ua{sv}s)", 2, V({"max": 2000, "min": 1000, "temp": 85, "resp": 1}), "quick")
    st = wait_test_done(env)
    assert st["tests"][-1]["passed"] is False and st["tests"][-1]["reason"] == "gpu_errors", st
    assert st["slots"][2]["state"] == "empty" and env.gov_state()["max"] == 1850, st


@integration()
def I13_governor_down_is_reported_and_service_survives(env):
    env.stop_governor()
    st = env.call("GetState")
    assert st["actual"] == {"error": "ServiceUnavailable"}, st
    r = env.call("SetProfile", "(s)", "performance")
    assert err(r), r
    assert env.stoned.poll() is None
    env.start_governor()
    assert env.call("SetProfile", "(s)", "performance")["actual"]["max"] == 2000


@integration()
def I14_per_game_profile_is_restored_even_if_the_game_crashes(env):
    env.call("SetGameProfile", "(ss)", "1091500", "performance")
    env.call("GameStarted", "(s)", "1091500")
    assert env.gov_state()["max"] == 2000
    r = env.call("GameEnded", "(sb)", "1091500", False)
    assert env.gov_state()["max"] == 1850 and "game" not in r, r


@integration()
def I15_home_ready_applies_only_validated_profiles(env):
    env.kill_stoned()
    s = env.store().load()
    s["slots"][0] = {"max": 2000, "min": 1000, "temp": 85, "resp": 1, "state": "tested", "test_level": "quick"}
    s["slots"][1] = {"max": 1900, "min": 1000, "temp": 80, "resp": 1, "state": "validated", "test_level": "standard"}
    s["profile"] = "c2"
    env.store().save(s)
    R.mark_clean_shutdown(env.store())            # orderly reboot
    env.recover()
    env.start_stoned()
    assert env.gov_state()["max"] == 1850         # nothing applied before Home
    r = env.call("HomeReady")
    assert r["applied"] == "c2" and env.gov_state()["max"] == 1900, r


@integration(test_seconds=3)
def I21_standard_test_fails_when_clock_is_not_sustained(env):
    env.call("StartTest", "(ua{sv}s)", 0, V({"max": 1950, "min": 1000, "temp": 80, "resp": 1}), "standard")
    st = wait_test_done(env)                       # fake clock stays at 1500 MHz, no throttling
    assert st["tests"][-1]["reason"] == "clock_not_sustained" and st["slots"][0]["state"] == "empty", st


@integration()
def I22_boot_without_reaching_home_is_counted(env):
    env.kill_stoned()
    r = env.recover()                              # previous boot never called HomeReady
    assert r["fail_count"] == 1, r


@integration()
def I23_discard_keeps_the_previous_slot(env):
    old = {"max": 1900, "min": 1000, "temp": 80, "resp": 1}
    env.call("StartTest", "(ua{sv}s)", 0, V(old), "quick"); wait_test_done(env); env.call("SaveTestResult")
    env.call("StartTest", "(ua{sv}s)", 0, V({**old, "max": 2000}), "quick"); wait_test_done(env)
    st = env.call("DiscardTestResult")
    assert st["slots"][0]["max"] == 1900 and "pending_result" not in st, st
    assert "InvalidValue" in err(env.call("SaveTestResult"))


@integration()
def I24_quick_menu_session_profile_ends_with_the_game(env):
    assert "InvalidValue" in err(env.call("SetSessionProfile", "(s)", "performance"))   # no game
    env.call("SetProfile", "(s)", "silent")
    env.call("GameStarted", "(s)", "570")
    r = env.call("SetSessionProfile", "(s)", "performance")
    assert r["actual"]["max"] == 2000 and r["profile"] == "silent" and r["game"]["profile"] == "performance", r
    r = env.call("GameEnded", "(sb)", "570", True)
    assert r["actual"]["max"] == 1500 and r["profile"] == "silent", r


@integration()
def I25_restore_defaults_leaves_safe_mode(env):
    env.kill_stoned()
    s = env.store().load(); s["safe_mode"] = True; s["fail_count"] = 2; env.store().save(s)
    env.start_stoned()
    r = env.call("RestoreDefaults")
    assert not r["safe_mode"] and r["fail_count"] == 0, r
    assert env.call("SetProfile", "(s)", "performance")["actual"]["max"] == 2000


@integration(polkit_off=False)
def I16_without_polkit_every_write_is_denied(env):
    assert "board" in env.call("GetCapabilities")
    for m, sig, args in (("SetProfile", "(s)", ("performance",)), ("RestoreDefaults", None, ()),
                         ("StartTest", "(ua{sv}s)", (0, V({"max": 1900, "min": 1000, "temp": 80, "resp": 1}), "quick"))):
        assert "PermissionDenied" in err(env.call(m, sig, *args)), m
    assert [c for c in env.gov_calls() if c["call"].startswith("Set")] == []


@integration()
def I17_wrong_argument_types_are_rejected(env):
    r = env.call("SetProfile", "(i)", 3)
    assert err(r), r
    assert env.stoned.poll() is None


@integration()
def I18_metrics_are_real_readings_or_absent(env):
    m = env.call("GetMetrics")
    assert m["gpu_temp_c"] == 55.0 and m["cpu_temp_c"] == 48.0 and m["fan_rpm"] == 1450, m
    assert m["gpu_clock_mhz"] == 1500 and m["gpu_power_w"] == 61.0 and m["vram_used_mb"] == 300, m


@integration(workload=("false",))
def I19_workload_that_dies_fails_the_test(env):
    env.call("StartTest", "(ua{sv}s)", 0, V({"max": 1900, "min": 1000, "temp": 80, "resp": 1}), "quick")
    st = wait_test_done(env)
    assert st["tests"][-1]["reason"] == "workload_failed" and st["slots"][0]["state"] == "empty", st


@integration(test_seconds=30)
def I20_cancel_restores_and_saves_nothing(env):
    env.call("StartTest", "(ua{sv}s)", 0, V({"max": 2000, "min": 1000, "temp": 85, "resp": 1}), "quick")
    r = env.call("CancelTest")
    assert "testing" not in r and r["slots"][0]["state"] == "empty" and env.gov_state()["max"] == 1850, r
    assert env.store().get_flag(S.FLAG_TRIAL) is None



# ---------------------------------------------------------------- step 7: telemetry and thermal watch
def collect(env, signal, seconds, conn=None):
    got = []
    bus = conn or env.bus
    sid = bus.signal_subscribe("org.stonemachine.Performance1", "org.stonemachine.Performance1", signal,
                               "/org/stonemachine/Performance1", None, 0,
                               lambda c, s, p, i, n, params: got.append(params.unpack()[0]))
    end = time.time() + seconds
    ctx = GLib.MainContext.default()
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.01)
    bus.signal_unsubscribe(sid)
    return got


@integration()
def T01_metrics_are_real_readings(env):
    m = env.call("GetMetrics")
    write_proc_stat(env.sysfs, 1300, 9700)          # +300 busy, +700 idle -> 30 %
    m = env.call("GetMetrics")
    assert m["cpu_pct"] == 30.0 and m["gpu_busy_pct"] == 42.5, m
    assert m["ram_used_mb"] == 3907 and m["ram_total_mb"] == 15625 and m["gpu_power_w"] == 61.0, m
    assert m["missing"] == [], m


@integration(fan=False)
def T02_missing_sensors_are_reported_not_guessed(env):
    write_gpu_metrics(env.sysfs, 0xFFFF)            # governor's fix-metrics not active
    m = env.call("GetMetrics")
    assert "fan_rpm" not in m and "gpu_busy_pct" not in m, m
    assert "fan_rpm" in m["missing"] and "gpu_busy_pct" in m["missing"], m


@integration()
def T03_no_subscriber_means_no_sampling(env):
    got = collect(env, "Metrics", 2.2)
    assert got == [], got
    st = env.call("Subscribe", "(s)", "performance_center")
    assert st["rate_ms"] == 1000, st
    got = collect(env, "Metrics", 3.2)
    assert 2 <= len(got) <= 4 and "gpu_temp_c" in got[-1], len(got)
    assert env.call("Subscribe", "(s)", "overlay")["rate_ms"] == 500
    got = collect(env, "Metrics", 2.1)
    assert 3 <= len(got) <= 5, len(got)
    assert env.call("Unsubscribe")["rate_ms"] == 0
    assert collect(env, "Metrics", 1.5) == []
    assert "InvalidValue" in err(env.call("Subscribe", "(s)", "everything"))


@integration()
def T04_a_client_that_disappears_stops_its_sampling(env):
    other = Gio.DBusConnection.new_for_address_sync(
        env.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    other.call_sync("org.stonemachine.Performance1", "/org/stonemachine/Performance1", "org.stonemachine.Performance1",
                    "Subscribe", GLib.Variant("(s)", ("overlay",)), None, 0, 5000, None)
    assert env.call("GetState")["telemetry"]["rate_ms"] == 500
    other.close_sync(None)
    time.sleep(0.5)
    st = env.call("GetState")["telemetry"]
    assert st["rate_ms"] == 0 and st["subscribers"] == 0, st


@integration(thermal_scale=0.05)
def T05_thermal_watch_reduces_then_recovers(env):
    env.call("SetProfile", "(s)", "performance")
    set_gpu_temp(env.sysfs, 90)
    ev = collect(env, "ThermalEvent", 4.0)
    levels = [e["level"] for e in ev]
    assert "warning" in levels and "reduced" in levels, levels
    st = env.call("GetState")
    assert env.gov_state()["max"] == 1500 and st["thermal"]["reduced"] and st["profile"] == "performance", st
    assert "ThermalLimit" in err(env.call("SetProfile", "(s)", "balanced"))
    assert env.call("SetProfile", "(s)", "silent")["actual"]["max"] == 1500
    set_gpu_temp(env.sysfs, 60)
    ev = collect(env, "ThermalEvent", 5.0)
    assert [e["level"] for e in ev][-1:] == ["normal"], ev
    st = env.call("GetState")
    assert not st["thermal"]["reduced"] and env.gov_state()["max"] == 1500 and st["profile"] == "silent", st


@integration(thermal_scale=0.05)
def T06_running_hot_on_a_custom_slot_removes_its_validation(env):
    env.kill_stoned()
    s = env.store().load()
    s["slots"][0] = {"max": 2000, "min": 1000, "temp": 85, "resp": 1, "state": "validated", "test_level": "standard"}
    s["profile"] = s["last_known_good"] = "c1"
    env.store().save(s)
    env.start_stoned()
    env.call("HomeReady")
    assert env.gov_state()["max"] == 2000
    set_gpu_temp(env.sysfs, 90)
    collect(env, "ThermalEvent", 4.0)
    st = env.call("GetState")
    assert st["slots"][0]["state"] == "tested" and st["profile"] == "balanced" and st["last_known_good"] == "balanced", st
    set_gpu_temp(env.sysfs, 60)
    collect(env, "ThermalEvent", 5.0)
    assert env.gov_state()["max"] == 1850                 # back to BALANCED, not to the slot


@integration(thermal_scale=0.05)
def T07_fan_stopping_while_hot_reduces_at_once(env):
    env.call("SetProfile", "(s)", "performance")
    set_gpu_temp(env.sysfs, 72)
    collect(env, "ThermalEvent", 0.6)
    set_fan(env.sysfs, 0)
    ev = collect(env, "ThermalEvent", 1.5)
    assert any(e["level"] == "reduced" and e["reason"] == "fan" for e in ev), ev
    assert env.gov_state()["max"] == 1500
    set_gpu_temp(env.sysfs, 60)
    collect(env, "ThermalEvent", 4.0)
    assert env.call("GetState")["thermal"]["reduced"], "no recovery while the fan reads 0"


@integration(thermal_scale=0.05)
def T08_critical_is_notified_never_acted_on_beyond_silent(env):
    set_gpu_temp(env.sysfs, 93)
    ev = collect(env, "ThermalEvent", 4.0)
    assert [e["level"] for e in ev].count("critical") == 1, ev
    assert env.stoned.poll() is None and env.gov_state()["max"] == 1500
    calls = [c for c in env.gov_calls() if c["call"] == "SetTemperatureThresholds"]
    assert all(c["args"][0] > 0 and c["args"][0] <= 85 for c in calls), calls


@integration()
def T09_sampling_cost(env):
    for _ in range(50):
        env.call("GetMetrics")
    st = env.call("GetState")["telemetry"]
    print(f"      coste medio por lectura completa: {st['avg_sample_ms']} ms")
    assert st["avg_sample_ms"] < 5, st


# ---------------------------------------------------------------- step 9: Safe Mode without the interface
def ctl(env, *args):
    r = subprocess.run([PY, "-c", "import sys; from stoned.ctl import main; sys.exit(main(sys.argv[1:]))", *args],
                       env=env.env, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout


@integration()
def S01_enter_safe_mode_applies_at_once(env):
    env.call("SetProfile", "(s)", "performance")
    env.call("SetGameProfile", "(ss)", "570", "performance")
    r = env.call("EnterSafeMode")
    assert r["safe_mode"] and r["safe_reason"] == "manual" and r["profile"] == "balanced", r
    assert env.gov_state()["max"] == 1850 and r["per_game"] == {"570": "performance"}, r     # kept, unused
    env.call("GameStarted", "(s)", "570")
    assert env.gov_state()["max"] == 1850, "per-game profile ignored in Safe Mode"
    assert "SafeMode" in err(env.call("SetProfile", "(s)", "performance"))


@integration()
def S02_manual_safe_mode_survives_reboots_until_left(env):
    env.call("EnterSafeMode")
    env.kill_stoned()
    R.mark_clean_shutdown(env.store())
    rep = env.recover()
    assert rep["safe_mode"] and rep["safe_reason"] == "manual", rep
    env.start_stoned()
    r = env.call("HomeReady")
    assert r["safe_mode"] and env.gov_state()["max"] == 1850, r
    r = env.call("ExitSafeMode")
    assert not r["safe_mode"] and "safe_reason" not in r, r
    env.kill_stoned()
    R.mark_clean_shutdown(env.store())
    assert not env.recover()["safe_mode"]


@integration()
def S03_kernel_parameter_cannot_be_left_from_the_interface(env):
    env.kill_stoned()
    with open(env.cmdline, "w") as f:
        f.write("quiet stone.safe=1\n")
    rep = env.recover()
    assert rep["safe_reason"] == "kernel", rep
    env.start_stoned()
    assert "SafeMode" in err(env.call("ExitSafeMode"))
    code, out = ctl(env, "safe-mode", "off")
    assert code == 1 and "stone.safe=1" in out, out


@integration()
def S04_stonectl_status_and_safe_mode_over_dbus(env):
    code, out = ctl(env, "status")
    assert code == 0 and "Perfil: balanced" in out and "Safe Mode: no" in out and "1000–1850 MHz" in out, out
    code, out = ctl(env, "safe-mode", "on")
    assert code == 0 and "activado" in out and env.call("GetState")["safe_mode"], out
    code, out = ctl(env, "status")
    assert "Safe Mode: sí — activado por el usuario" in out, out
    code, out = ctl(env, "restore-defaults")
    assert code == 0 and not env.call("GetState")["safe_mode"], out


@integration()
def S05_stonectl_works_when_stoned_is_down(env):
    env.kill_stoned()
    code, out = ctl(env, "status")
    assert code == 1 and "no responde" in out, out
    code, out = ctl(env, "safe-mode", "on")
    assert code == 0 and "próximo arranque" in out, out
    assert env.store().get_flag(S.FLAG_SAFE_REQUEST) is not None
    rep = env.recover()
    assert rep["safe_mode"] and rep["safe_reason"] == "manual", rep
    code, out = ctl(env, "safe-mode", "off")
    rep = env.recover()
    assert not rep["safe_mode"] and rep["fail_count"] == 0, rep


@integration()
def S06_boot_loop_with_a_broken_interface_ends_in_safe_mode(env):
    env.kill_stoned()
    s = env.store().load()
    s["slots"][0] = {"max": 2000, "min": 1000, "temp": 85, "resp": 1, "state": "validated", "test_level": "standard"}
    s["profile"] = s["last_known_good"] = "c1"
    s["per_game"] = {"570": "c1"}
    env.store().save(s)
    env.recover(); rep = env.recover()             # two boots where Home never came up
    assert rep["safe_mode"] and rep["safe_reason"] == "failures" and rep["profile"] == "balanced", rep
    env.start_stoned()
    env.call("GameStarted", "(s)", "570")
    assert env.gov_state()["max"] == 1850


@integration()
def S07_safe_mode_blocks_tests_and_session_profiles(env):
    env.call("EnterSafeMode")
    r = env.call("StartTest", "(ua{sv}s)", 0, V({"max": 1800, "min": 1000, "temp": 80, "resp": 1}), "quick")
    assert err(r), r
    env.call("GameStarted", "(s)", "570")
    assert "SafeMode" in err(env.call("SetSessionProfile", "(s)", "performance"))
    assert env.call("SetSessionProfile", "(s)", "silent")["actual"]["max"] == 1500


# ---------------------------------------------------------------- step 10: Steam game sessions
from stoned import games as GM  # noqa: E402


def fake_proc(env, pid, argv):
    d = os.path.join(env.sysfs, "proc", str(pid))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "cmdline"), "wb") as f:
        f.write(b"\0".join(a.encode() for a in argv) + b"\0")


def end_proc(env, pid):
    shutil.rmtree(os.path.join(env.sysfs, "proc", str(pid)), ignore_errors=True)


REAPER = "/home/stone/.local/share/Steam/ubuntu12_32/reaper"


def steam_game(env, pid, appid):
    fake_proc(env, pid, [REAPER, "SteamLaunch", f"AppId={appid}", "--", "/home/stone/.local/share/Steam/ubuntu12_32/steam-launch-wrapper", "--", "game.exe"])


def wait_for(fn, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return fn()


@test
def G01_reaper_command_line_parsing():
    assert GM.parse_reaper([REAPER, "SteamLaunch", "AppId=570", "--", "x"]) == "570"
    assert GM.parse_reaper([REAPER, "SteamLaunch", "AppId=2929382885", "--"]) == "2929382885"   # non-Steam shortcut
    assert GM.parse_reaper(["/usr/bin/bash", "SteamLaunch", "AppId=570"]) is None
    assert GM.parse_reaper([REAPER, "AppId=570"]) is None
    assert GM.parse_reaper([REAPER, "SteamLaunch", "AppId=570;rm -rf /"]) is None
    assert GM.parse_reaper([REAPER, "SteamLaunch", "AppId="]) is None
    assert GM.parse_reaper([]) is None and GM.parse_reaper(None) is None


@integration(game_source="steam")
def G02_steam_game_is_detected_and_its_profile_applied(env):
    env.call("SetGameProfile", "(ss)", "1091500", "performance")
    assert "Unsupported" in err(env.call("GameStarted", "(s)", "1091500"))     # nobody can fake a session
    steam_game(env, 4242, "1091500")
    st = wait_for(lambda: (lambda s: s if s["games"].get("running") == "1091500" else None)(env.call("GetState")))
    assert st and st["game"]["profile"] == "performance" and env.gov_state()["max"] == 2000, st
    end_proc(env, 4242)
    st = wait_for(lambda: (lambda s: s if "running" not in s["games"] else None)(env.call("GetState")))
    assert st and "game" not in st and env.gov_state()["max"] == 1850, st


def _tested_slot_in_use(env):
    env.kill_stoned()
    s = env.store().load()
    s["slots"][0] = {"max": 1950, "min": 1000, "temp": 80, "resp": 1, "state": "tested", "test_level": "standard"}
    env.store().save(s)
    env.start_stoned()
    env.call("SetProfile", "(s)", "c1")


@integration(game_source="steam", min_session_s=60)
def G03_short_session_does_not_validate(env):
    _tested_slot_in_use(env)
    steam_game(env, 5000, "570")
    wait_for(lambda: env.call("GetState")["games"].get("running"))
    end_proc(env, 5000)
    st = wait_for(lambda: (lambda s: s if "running" not in s["games"] else None)(env.call("GetState")))
    assert st["slots"][0]["state"] == "tested" and st["provisional"], st


@integration(game_source="steam", min_session_s=1)
def G04_clean_session_validates_a_tested_slot(env):
    _tested_slot_in_use(env)
    steam_game(env, 5001, "570")
    wait_for(lambda: env.call("GetState")["games"].get("running"))
    time.sleep(1.2)
    end_proc(env, 5001)
    st = wait_for(lambda: (lambda s: s if s["slots"][0]["state"] == "validated" else None)(env.call("GetState")))
    assert st and st["last_known_good"] == "c1" and not st["provisional"], env.call("GetState")


@integration(game_source="steam", min_session_s=1, gpu_errors=True)
def G05_gpu_errors_during_the_session_never_validate(env):
    _tested_slot_in_use(env)
    steam_game(env, 5002, "570")
    wait_for(lambda: env.call("GetState")["games"].get("running"))
    time.sleep(1.2)
    end_proc(env, 5002)
    st = wait_for(lambda: (lambda s: s if "running" not in s["games"] else None)(env.call("GetState")))
    assert st["slots"][0]["state"] == "tested", st


@integration(game_source="steam")
def G06_game_running_when_stoned_restarts_is_picked_up(env):
    env.call("SetGameProfile", "(ss)", "570", "silent")
    steam_game(env, 6000, "570")
    wait_for(lambda: env.call("GetState")["games"].get("running"))
    env.kill_stoned()
    env.start_stoned()
    st = env.call("GetState")
    assert st["games"]["running"] == "570" and st["game"]["profile"] == "silent", st


@integration(game_source="steam")
def G07_other_processes_are_ignored_and_scanning_is_cheap(env):
    for pid in range(10000, 10400):
        fake_proc(env, pid, ["/usr/bin/some-daemon", "--flag", str(pid)])
    fake_proc(env, 10500, ["/usr/bin/python3", "SteamLaunch", "AppId=570"])       # not reaper
    fake_proc(env, 10501, [REAPER, "-v"])                                          # reaper, no launch
    time.sleep(4.5)
    st = env.call("GetState")
    assert "running" not in st["games"], st
    print(f"      escaneo de /proc (400 procesos nuevos, luego sólo cambios): {st['games']['avg_scan_ms']} ms de media")
    assert st["games"]["avg_scan_ms"] < 20, st


# ---------------------------------------------------------------- step 12: image (governor config)
GOVCFG = os.path.join(REPO, "build_files", "governor-config.py")
SAMPLE_CFG = """[timing]
burst-samples = 60

[gpu]
set-method = "smu"

[dbus]
enabled = false

[frequency-range]
min = 1000
max = 1850

[temperature]
throttling = 85
throttling_recovery = 75

[[safe-points]]
frequency = 1000
voltage = 800

[[safe-points]]
frequency = 2000
voltage = 960
"""


def _govcfg(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config.toml")
    open(p, "w").write(text)
    r = subprocess.run([PY, GOVCFG, p], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr, open(p).read()


@test
def B01_governor_config_only_enables_dbus():
    code, out, after = _govcfg(SAMPLE_CFG)
    assert code == 0 and "STONE_GOV_RANGE=1000-1850" in out and "1000@800,2000@960" in out, out
    assert after == SAMPLE_CFG.replace("enabled = false", "enabled = true"), after


@test
def B02_governor_config_refuses_bad_packages():
    assert _govcfg(SAMPLE_CFG.replace("[dbus]\nenabled = false\n", ""))[0] == 1          # no D-Bus
    assert _govcfg(SAMPLE_CFG.replace("max = 1850", "max = 2300"))[0] == 1                # beyond stable
    assert _govcfg(SAMPLE_CFG.replace("throttling = 85", "throttling = 0"))[0] == 1       # protection off
    assert _govcfg("not toml [[[")[0] == 1
    code, _, after = _govcfg(SAMPLE_CFG.replace("enabled = false", "enabled = true"))
    assert code == 0 and after.count("enabled = true") == 1


# ---------------------------------------------------------------- step 13: BEFORE / AFTER
def _quick_run(env, method, sig, *args):
    r = env.call(method, sig, *args)
    assert not err(r), r
    return wait_test_done(env)["tests"][-1]


@integration()
def C01_more_clock_that_is_sustained_is_better(env):
    set_gpu_clock(env.sysfs, 1840)
    b = _quick_run(env, "MeasureBaseline", "(s)", "quick")
    assert b["kind"] == "baseline" and b["profile"] == "balanced" and b["passed"], b
    assert env.call("GetState")["actual"]["max"] == 1850
    set_gpu_clock(env.sysfs, 1930)
    t = _quick_run(env, "StartTest", "(ua{sv}s)", 0, V({"max": 1950, "min": 1000, "temp": 85, "resp": 1}), "quick")
    c = t["comparison"]
    assert c["verdict"] == "better" and c["clock_gain_pct"] == 4.9 and c["baseline_profile"] == "balanced", c


@integration()
def C02_more_ceiling_without_more_clock_is_no_gain(env):
    set_gpu_clock(env.sysfs, 1840)
    _quick_run(env, "MeasureBaseline", "(s)", "quick")
    set_gpu_clock(env.sysfs, 1850)
    t = _quick_run(env, "StartTest", "(ua{sv}s)", 0, V({"max": 2000, "min": 1000, "temp": 85, "resp": 1}), "quick")
    assert t["comparison"]["verdict"] == "no_gain", t["comparison"]


@integration()
def C03_throttling_makes_it_worse(env):
    set_gpu_clock(env.sysfs, 1840)
    _quick_run(env, "MeasureBaseline", "(s)", "quick")
    set_gpu_clock(env.sysfs, 1880)
    set_gpu_temp(env.sysfs, 79)
    t = _quick_run(env, "StartTest", "(ua{sv}s)", 0, V({"max": 2000, "min": 1000, "temp": 80, "resp": 1}), "quick")
    c = t["comparison"]
    assert t["throttle_pct"] == 100.0 and c["verdict"] == "worse", (t, c)


@integration()
def C04_without_a_baseline_the_comparison_says_so(env):
    t = _quick_run(env, "StartTest", "(ua{sv}s)", 0, V({"max": 1950, "min": 1000, "temp": 85, "resp": 1}), "quick")
    assert t["comparison"] == {"verdict": "insufficient"}, t


@integration(test_seconds=30)
def C05_cancelling_a_baseline_touches_nothing(env):
    env.call("SetProfile", "(s)", "silent")
    before = len(env.gov_calls())
    env.call("MeasureBaseline", "(s)", "quick")
    st = env.call("CancelTest")
    assert "testing" not in st and env.gov_state()["max"] == 1500, st
    assert len(env.gov_calls()) == before, env.gov_calls()[before:]

# ---------------------------------------------------------------- runner
def main():
    only = sys.argv[1:]
    passed = failed = 0
    for fn in RESULTS:
        if only and not any(o in fn.__name__ for o in only):
            continue
        t0 = time.time()
        try:
            fn()
            passed += 1
            print(f"PASS  {fn.__name__}  ({time.time() - t0:.1f}s)", flush=True)
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}", flush=True)
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
