"""stone-cu40 tests: fake sysfs, fake rpm-ostree, journalctl and stone-cu-verify.

Nothing touches the real boot configuration or GPU. Passing means CODE TESTED, never
HARDWARE VERIFIED.

    python3 tests/test_cu40.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "system_files", "usr", "lib", "stone", "stone_cu40.py")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Box:
    def __init__(self, mode=3, karg=True, temp=50, verify_exit=0, verify_temp=None, clock=1500,
                 prev_log="", now_log="amdgpu: SE 2, SH per SE 2, CU per SH 10, active_cu_number 40", safe=False):
        self.tmp = tempfile.mkdtemp(prefix="cu40-")
        r = lambda *p: os.path.join(self.tmp, *p)
        self.root, self.bin = r("root"), r("bin")
        dev = r("root/sys/class/drm/card1/device")
        os.makedirs(os.path.join(dev, "hwmon/hwmon2"))
        os.makedirs(self.bin)
        self.w("root/sys/class/drm/card1/device/vendor", "0x1002")
        self.w("root/sys/class/drm/card1/device/device", "0x13fe")
        self.w("root/sys/class/drm/card1/device/hwmon/hwmon2/temp1_input", temp * 1000)
        self.w("root/sys/class/drm/card1/device/pp_dpm_sclk", f"0: 1000Mhz\n1: {clock}Mhz *\n")
        if mode is not None:
            self.w("root/sys/module/amdgpu/parameters/bc250_cc_write_mode", mode)
        self.w("cmdline", "BOOT_IMAGE=x quiet" + (" amdgpu.bc250_cc_write_mode=3" if karg else "") + (" stone.safe=1" if safe else ""))
        self.w("stone-release", 'STONE_CU40="ok"\nSTONE_CU40_SOURCE="https://github.com/OpenGamingCollective/linux.git v7.2.9-ogc3 x"')
        self.w("prev.log", prev_log)
        self.w("now.log", now_log)
        self.script("rpm-ostree", f'echo "$@" >> {r("rpmostree.calls")}\nexit 0')
        self.script("journalctl", f'case "$*" in *"-b -1"*) cat {r("prev.log")};; *) cat {r("now.log")};; esac')
        hot = (f"echo {verify_temp * 1000} > {dev}/hwmon/hwmon2/temp1_input\n" if verify_temp else "")
        self.script("verify", f"{hot}echo device=fake\necho 'pass=0 seconds=0.5 errors={0 if verify_exit == 0 else 7}'\n"
                    f"sleep 1.5\necho summary\nexit {verify_exit}")
        self.w("bin/verify.spv", "x")
        self.env = dict(os.environ, STONE_SYSFS_ROOT=self.root, STONE_CMDLINE=r("cmdline"), STONE_RELEASE=r("stone-release"),
                        STONE_CU40_STATE_DIR=r("state"), STONE_CU_VERIFY=r("bin/verify"), STONE_CU40_ALLOW_USER="1",
                        PATH=self.bin + ":" + os.environ["PATH"])

    def w(self, rel, v):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(f"{v}\n")

    def script(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)

    def run(self, *args, stdin=""):
        r = subprocess.run([sys.executable, TOOL, *args], input=stdin, capture_output=True, text=True, env=self.env, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def calls(self):
        try:
            return open(os.path.join(self.tmp, "rpmostree.calls")).read().split("\n")[:-1]
        except OSError:
            return []

    def state(self):
        with open(os.path.join(self.tmp, "state", "state.json")) as f:
            return json.load(f)

    def set_state(self, st):
        os.makedirs(os.path.join(self.tmp, "state"), exist_ok=True)
        with open(os.path.join(self.tmp, "state", "state.json"), "w") as f:
            json.dump(dict({"since": 1, "reason": None, "boots": 0, "tests": []}, **st), f)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def box(**kw):
    def deco(fn):
        def run():
            b = Box(**kw)
            try:
                fn(b)
            finally:
                b.close()
        run.__name__ = fn.__name__
        TESTS.append(run)
        return run
    return deco


@box(mode=None, karg=False)
def C01_status_says_when_the_image_has_no_unlock(b):
    rc, out = b.run("status")
    assert rc == 0 and "NO lo trae" in out and "24 CU (normal)" in out, out
    rc, out = b.run("enable", stdin="ACTIVAR\n")
    assert rc == 1 and "no trae el desbloqueo" in out and b.calls() == [], out


@box(mode=0, karg=False)
def C02_enable_needs_the_typed_confirmation(b):
    rc, out = b.run("enable", stdin="si\n")
    assert rc == 1 and "Cancelado" in out and b.calls() == [], out
    rc, out = b.run("enable", stdin="ACTIVAR\n")
    assert rc == 0 and b.calls() == ["kargs --append-if-missing=amdgpu.bc250_cc_write_mode=3"], (out, b.calls())
    assert b.state()["state"] == "trial", b.state()
    rc, out = b.run("status")
    assert "falta la prueba" in out and "próximo arranque" in out, out


@box(mode=0, karg=False, temp=80)
def C03_enable_refuses_a_hot_gpu(b):
    rc, out = b.run("enable", stdin="ACTIVAR\n")
    assert rc == 1 and "80 °C" in out and b.calls() == [], out


@box(mode=0, karg=False, safe=True)
def C04_enable_refuses_in_safe_mode(b):
    rc, out = b.run("enable", stdin="ACTIVAR\n")
    assert rc == 1 and "Modo seguro" in out and b.calls() == [], out


@box()
def C05_passed_test_keeps_40_cu(b):
    b.set_state({"state": "trial"})
    rc, out = b.run("test", "--minutes", "1")
    assert rc == 0 and "APROBADA" in out, out
    st = b.state()
    assert st["state"] == "confirmed" and st["tests"][-1]["ok"] and st["tests"][-1]["cus"] == 40, st
    assert b.calls() == [], b.calls()


@box(verify_exit=2)
def C06_wrong_results_turn_40_cu_off(b):
    b.set_state({"state": "trial"})
    rc, out = b.run("test", "--minutes", "1")
    assert rc == 2 and "resultados incorrectos" in out and "vuelve a 24 CU" in out, out
    assert b.calls() == ["kargs --delete-if-present=amdgpu.bc250_cc_write_mode=3"], b.calls()
    assert b.state()["state"] == "reverted", b.state()


@box(clock=2000)
def C07_clock_above_the_ceiling_fails_the_test(b):
    b.set_state({"state": "trial"})
    rc, out = b.run("test", "--minutes", "1")
    assert rc == 2 and "2000 MHz" in out, out
    assert b.state()["state"] == "reverted" and b.calls(), (b.state(), b.calls())


@box(verify_temp=92)
def C08_test_stops_at_90_c(b):
    b.set_state({"state": "trial"})
    rc, out = b.run("test", "--minutes", "1")
    assert rc == 2 and "92 °C" in out, out
    assert b.state()["state"] == "reverted", b.state()


@box(now_log="amdgpu 0000:01:00.0: amdgpu: ring gfx_0.0.0 timeout, signaled seq=5")
def C09_gpu_errors_during_the_test_fail_it(b):
    b.set_state({"state": "trial"})
    rc, out = b.run("test", "--minutes", "1")
    assert rc == 2 and "errores de GPU" in out, out


@box(prev_log="Command line: quiet amdgpu.bc250_cc_write_mode=3\namdgpu 0000:01:00.0: amdgpu: GPU reset begin!")
def C10_guard_turns_off_after_a_boot_with_gpu_errors(b):
    b.set_state({"state": "confirmed"})
    rc, out = b.run("guard")
    assert rc == 0 and b.calls() == ["kargs --delete-if-present=amdgpu.bc250_cc_write_mode=3"], (out, b.calls())
    st = b.state()
    assert st["state"] == "reverted" and "arranque anterior" in st["reason"], st


@box(prev_log="Command line: quiet amdgpu.bc250_cc_write_mode=3\namdgpu: active_cu_number 40")
def C11_guard_leaves_a_clean_boot_alone(b):
    b.set_state({"state": "trial"})
    rc, out = b.run("guard")
    assert rc == 0 and b.calls() == [] and b.state()["state"] == "trial" and b.state()["boots"] == 1, (out, b.state())


@box(prev_log="Command line: quiet\namdgpu: GPU reset begin!")
def C12_guard_ignores_errors_from_a_24_cu_boot(b):
    b.set_state({"state": "trial"})
    b.run("guard")
    assert b.calls() == [] and b.state()["state"] == "trial", b.state()


@box(mode=0, karg=True)
def C13_disable_removes_the_argument(b):
    b.set_state({"state": "confirmed"})
    rc, out = b.run("disable")
    assert rc == 0 and b.calls() == ["kargs --delete-if-present=amdgpu.bc250_cc_write_mode=3"], b.calls()
    assert b.state()["state"] == "off", b.state()


@box()
def C14_bad_arguments_change_nothing(b):
    rc, out = b.run("test", "--minutes", "999")
    assert rc == 1 and b.calls() == [], out
    rc, out = b.run("whatever")
    assert rc == 1, out


def main():
    only = sys.argv[1:]
    failed = 0
    for fn in TESTS:
        if only and not any(o in fn.__name__ for o in only):
            continue
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{'all passed' if not failed else f'{failed} failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
