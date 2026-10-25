"""stone-cu40: the BC-250's 40 compute units, by hand and with a way back.

The BC-250 ships with 24 of its GPU's 40 compute units (CU) turned off. STONE OS's amdgpu has
the community unlock (duggasco/bc250-40cu-unlock) built in but off. Nothing here runs by
itself except the boot check, and that one can only turn the unlock off.

  stone-cu40 status            what this image has, what this boot uses, last test
  sudo stone-cu40 enable       asks for confirmation, then adds amdgpu.bc250_cc_write_mode=3
                               to the kernel arguments (rpm-ostree kargs); takes effect on reboot
  sudo stone-cu40 test [--minutes N]
                               GPU compute correctness test (stone-cu-verify) while watching
                               temperature, clock and the kernel log. Passed: 40 CU is kept.
                               Failed: 40 CU is turned off again (back to 24 CU on reboot).
  sudo stone-cu40 disable      removes the kernel argument; 24 CU from the next boot
  stone-cu40 guard             (systemd, at boot) GPU errors in the previous 40 CU boot ->
                               turned off again

Stable range: with 40 CU, stoned keeps the GPU at 1500 MHz at most (any profile) and the
governor's thermal limit stays; the test stops at 90 C. Every kernel-argument change makes a
new boot entry; the previous one (24 CU) stays in the boot menu.
"""

import json
import os
import re
import subprocess
import sys
import time

KARG = "amdgpu.bc250_cc_write_mode=3"
ACTIVE_MODES = {2, 3}
CEILING_MHZ = 1500
TEST_STOP_C = 90               # the test stops here; the governor throttles before
ENABLE_MAX_IDLE_C = 75         # too hot at rest to start
CLOCK_MARGIN_MHZ = 50
BC250_PCI = (0x1002, 0x13FE)
GPU_ERRORS = re.compile(r"ring \S+ timeout|gpu reset|gpu recovery|vm_l2_protection_fault|page fault|gpu hang"
                        r"|\*error\*|ib test failed|failed to initialize parser", re.I)
CU_COUNT = re.compile(r"active_cu_number (\d+)")
VERIFY = os.environ.get("STONE_CU_VERIFY", "/usr/libexec/stone/stone-cu-verify")


def _root():
    return os.environ.get("STONE_SYSFS_ROOT", "/")


def _p(path):
    return os.path.join(_root(), path.lstrip("/"))


def _state_path():
    return os.path.join(os.environ.get("STONE_CU40_STATE_DIR", "/var/lib/stone-cu40"), "state.json")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(path):
    v = _read(path)
    try:
        return int(v, 0) if v is not None else None
    except ValueError:
        return None


# ---- what the machine has --------------------------------------------------------------
def cmdline():
    return (_read(os.environ.get("STONE_CMDLINE", "/proc/cmdline")) or "").split()


def booted_with_karg():
    return KARG in cmdline()


def module_mode():
    """amdgpu's bc250_cc_write_mode for this boot; None = Bazzite's own amdgpu (no option)."""
    return _int(_p("sys/module/amdgpu/parameters/bc250_cc_write_mode"))


def gpu_device():
    import glob
    for dev in sorted(glob.glob(_p("sys/class/drm/card[0-9]*/device"))):
        if (_int(os.path.join(dev, "vendor")), _int(os.path.join(dev, "device"))) == BC250_PCI:
            return dev
    return None


def gpu_temp(dev):
    import glob
    for t in sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input"))):
        v = _int(t)
        if v is not None:
            return v // 1000
    return None


def gpu_clock(dev):
    for line in (_read(os.path.join(dev, "pp_dpm_sclk")) or "").splitlines():
        m = re.search(r"(\d+)\s*Mhz\s*\*", line, re.I)
        if m:
            return int(m.group(1))
    return None


def image_info():
    out = {}
    for line in (_read(os.environ.get("STONE_RELEASE", "/usr/lib/stone-release")) or "").splitlines():
        m = re.match(r'^(STONE_CU40(?:_SOURCE)?)="(.*)"$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _run(args, timeout=60):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def kernel_log(boot="0", since=None):
    args = ["journalctl", "-k", "--no-pager", "-o", "cat", "-b", boot]
    if since is not None:
        args.append(f"--since=@{int(since)}")
    try:
        r = _run(args, 30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def gpu_errors(log):
    return [line for line in log.splitlines() if "amdgpu" in line.lower() and GPU_ERRORS.search(line)]


def active_cus(log):
    found = CU_COUNT.findall(log or "")
    return int(found[-1]) if found else None


# ---- state ---------------------------------------------------------------------------------
def load():
    try:
        with open(_state_path()) as f:
            st = json.load(f)
        if isinstance(st, dict) and st.get("state") in ("off", "trial", "confirmed", "reverted"):
            st.setdefault("tests", [])
            return st
    except (OSError, ValueError):
        pass
    return {"state": "off", "since": None, "reason": None, "boots": 0, "tests": []}


def save(st):
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def set_state(st, state, reason=None):
    st.update(state=state, since=int(time.time()), reason=reason)
    if state == "trial":
        st["boots"] = 0
    save(st)


# ---- kernel argument -------------------------------------------------------------------------
def kargs(op):
    flag = {"add": f"--append-if-missing={KARG}", "remove": f"--delete-if-present={KARG}"}[op]
    try:
        r = _run(["rpm-ostree", "kargs", flag], 300)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def turn_off(st, reason):
    ok, msg = kargs("remove")
    set_state(st, "reverted" if reason else "off", reason)
    return ok, msg


# ---- commands --------------------------------------------------------------------------------------
def say(*lines):
    print("\n".join(lines))


def need_root():
    if os.geteuid() != 0 and os.environ.get("STONE_CU40_ALLOW_USER") != "1":
        say("Este comando cambia el arranque: ejecútalo con sudo, por ejemplo:  sudo stone-cu40 " + (sys.argv[1] if len(sys.argv) > 1 else ""))
        sys.exit(1)


STATE_TXT = {"off": "apagado", "trial": "activado, falta la prueba (sudo stone-cu40 test)",
             "confirmed": "activado y probado", "reverted": "apagado por seguridad"}


def cmd_status():
    st, info, mode, dev = load(), image_info(), module_mode(), gpu_device()
    log = kernel_log()
    cus = active_cus(log)
    say("STONE · 40 CU",
        f"  Esta versión de STONE OS : {'trae el desbloqueo' if mode is not None else 'NO lo trae (' + info.get('STONE_CU40', 'sin datos') + ')'}",
        f"  Este arranque            : {'40 CU (desbloqueo en uso)' if mode in ACTIVE_MODES else '24 CU (normal)'}"
        f"{'' if cus is None else f' · el driver cuenta {cus} CU'}",
        f"  Opción guardada          : {STATE_TXT[st['state']]}"
        f"{' · motivo: ' + st['reason'] if st.get('reason') else ''}",
        f"  Límite de GPU con 40 CU  : {CEILING_MHZ} MHz (lo aplica STONE en cualquier perfil)")
    if booted_with_karg() != (st["state"] in ("trial", "confirmed")) and st["state"] != "reverted":
        say("  Nota: el próximo arranque puede ser distinto a este (reinicia para aplicar el último cambio).")
    if dev:
        say(f"  GPU ahora                : {gpu_temp(dev)} °C · {gpu_clock(dev)} MHz")
    if log is None:
        say("  (Para ver el conteo de CU del driver: sudo stone-cu40 status)")
    if st["tests"]:
        t = st["tests"][-1]
        say(f"  Última prueba            : {'APROBADA' if t['ok'] else 'FALLÓ'} · {t.get('cus') or '?'} CU · "
            f"{t['minutes']} min · máx {t.get('peak_c')} °C / {t.get('peak_mhz')} MHz"
            f"{'' if t['ok'] else ' · ' + t.get('reason', '')}")
    return 0


def confirm(word):
    if not sys.stdin.isatty() and os.environ.get("STONE_CU40_ALLOW_USER") != "1":
        say("Hace falta escribir la confirmación en la consola; no se puede activar sin una persona.")
        return False
    try:
        return input(f"Para seguir escribe {word} y pulsa Enter (otra cosa cancela): ").strip() == word
    except EOFError:
        return False


def cmd_enable():
    need_root()
    st, mode, dev = load(), module_mode(), gpu_device()
    if dev is None:
        say("No se encontró la GPU de la BC-250 (PCI 1002:13fe). No se cambia nada.")
        return 1
    if mode is None:
        say("Esta versión de STONE OS no trae el desbloqueo de 40 CU "
            f"({image_info().get('STONE_CU40', 'sin datos')}). No se cambia nada.")
        return 1
    if "stone.safe=1" in cmdline():
        say("La consola arrancó en Modo seguro. Sal del Modo seguro antes de activar 40 CU.")
        return 1
    temp = gpu_temp(dev)
    if temp is not None and temp >= ENABLE_MAX_IDLE_C:
        say(f"La GPU está a {temp} °C sin jugar. Revisa la ventilación y vuelve a intentarlo en frío.")
        return 1
    if st["state"] in ("trial", "confirmed") and booted_with_karg():
        say("40 CU ya está activado. Para probarlo: sudo stone-cu40 test")
        return 0
    say("Vas a activar las 40 CU de la GPU (ahora usa 24).",
        "",
        "  · Es un cambio de la comunidad, no de AMD: no todas las placas lo aguantan.",
        f"  · Con 40 CU STONE limita la GPU a {CEILING_MHZ} MHz en todos los perfiles, porque",
        "    consume ~30 W más y se calienta más (rango estable medido por el autor).",
        "  · Después del reinicio corre la prueba: sudo stone-cu40 test",
        "    Si falla, STONE vuelve solo a 24 CU. Si hay errores de GPU, también.",
        "  · Si no arranca: en el menú de arranque elige la entrada anterior, o pulsa e y",
        "    agrega amdgpu.bc250_cc_write_mode=0 al final de la línea linux; luego Ctrl+X.",
        "")
    if not confirm("ACTIVAR"):
        say("Cancelado. No se cambió nada.")
        return 1
    ok, msg = kargs("add")
    if not ok:
        say("No se pudo cambiar el arranque: " + msg, "No se cambió nada.")
        return 1
    set_state(st, "trial")
    say("", "Listo. 40 CU se usará desde el próximo arranque.",
        "  1. Reinicia:            systemctl reboot",
        "  2. Después, la prueba:  sudo stone-cu40 test")
    return 0


def cmd_disable():
    need_root()
    st = load()
    ok, msg = turn_off(st, None)
    if not ok:
        say("No se pudo cambiar el arranque: " + msg)
        return 1
    say("40 CU apagado. Desde el próximo arranque la GPU vuelve a 24 CU.", "Reinicia: systemctl reboot")
    return 0


def watch_test(dev, minutes, poll=1.0):
    """Runs stone-cu-verify; returns (ok, reason, peak_c, peak_mhz, output)."""
    spv = VERIFY + ".spv"
    args = [VERIFY, spv, "--seconds", str(minutes * 60)]
    t0 = time.time()
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    peak_c = peak_mhz = 0
    reason = None
    import threading
    out = []
    reader = threading.Thread(target=lambda: out.extend(proc.stdout), daemon=True)
    reader.start()
    while proc.poll() is None:
        temp, mhz = gpu_temp(dev), gpu_clock(dev)
        peak_c, peak_mhz = max(peak_c, temp or 0), max(peak_mhz, mhz or 0)
        if temp is not None and temp >= TEST_STOP_C:
            reason = f"la GPU llegó a {temp} °C"
            proc.kill()
            break
        if time.time() - t0 > minutes * 60 + 120:
            reason = "la prueba no terminó (GPU colgada)"
            proc.kill()
            break
        time.sleep(poll)
    proc.wait()
    reader.join(2)
    text = "".join(out)
    if reason is None and proc.returncode == 2:
        reason = "la GPU dio resultados incorrectos (" + (re.findall(r"errors=(\d+)", text)[-1:] or ["?"])[0] + " errores)"
    elif reason is None and proc.returncode != 0:
        reason = "la prueba no pudo correr: " + (text.strip().splitlines()[-1:] or ["sin detalle"])[0]
    log = kernel_log(since=t0)
    if reason is None and log is None:
        reason = "no se pudo leer el registro del kernel"
    if reason is None and gpu_errors(log or ""):
        reason = "el kernel registró errores de GPU: " + gpu_errors(log)[0][:120]
    return reason is None, reason, peak_c, peak_mhz, text


def cmd_test(argv):
    need_root()
    minutes = 5
    if argv[:1] == ["--minutes"] and len(argv) == 2 and argv[1].isdigit() and 1 <= int(argv[1]) <= 60:
        minutes = int(argv[1])
    elif argv:
        say("Uso: sudo stone-cu40 test [--minutes N]   (N de 1 a 60; por defecto 5)")
        return 1
    st, dev, mode = load(), gpu_device(), module_mode()
    if dev is None:
        say("No se encontró la GPU de la BC-250.")
        return 1
    if not (os.access(VERIFY, os.X_OK) and os.path.exists(VERIFY + ".spv")):
        say("Esta versión de STONE OS no trae la prueba (stone-cu-verify).")
        return 1
    active = mode in ACTIVE_MODES
    say(f"Prueba de la GPU con {'40' if active else '24'} CU: {minutes} min de cálculo comprobado contra la CPU.",
        f"Se detiene si la GPU llega a {TEST_STOP_C} °C. No apagues la consola.", "")
    ok, reason, peak_c, peak_mhz, out = watch_test(dev, minutes)
    if ok and active and peak_mhz > CEILING_MHZ + CLOCK_MARGIN_MHZ:
        ok, reason = False, f"la GPU subió a {peak_mhz} MHz con 40 CU (límite {CEILING_MHZ})"
    cus = active_cus(kernel_log() or "")
    st["tests"] = (st["tests"] + [{"time": int(time.time()), "ok": ok, "cus": cus or (40 if active else 24),
                                    "minutes": minutes, "peak_c": peak_c, "peak_mhz": peak_mhz,
                                    "reason": reason or ""}])[-5:]
    passes = re.findall(r"^pass=", out, re.M)
    say(f"  Pasadas: {len(passes)} · máx {peak_c} °C · máx {peak_mhz} MHz")
    if ok:
        if active:
            set_state(st, "confirmed")
            say("", "APROBADA. Las 40 CU calcularon bien. Queda activado.")
        else:
            save(st)
            say("", "APROBADA (con 24 CU). 40 CU no está en uso en este arranque.")
        return 0
    if active:
        okk, msg = turn_off(st, reason)
        say("", "FALLÓ: " + reason + ".",
            "STONE apagó 40 CU: desde el próximo arranque la GPU vuelve a 24 CU." if okk
            else "No se pudo apagar solo (" + msg + "). Ejecuta: sudo stone-cu40 disable",
            "Reinicia: systemctl reboot")
    else:
        save(st)
        say("", "FALLÓ con 24 CU: " + reason + ". Revisa la ventilación y la fuente antes de activar 40 CU.")
    return 2


def cmd_guard():
    """At boot. Only ever turns 40 CU off."""
    st = load()
    if st["state"] not in ("trial", "confirmed") or not booted_with_karg():
        return 0
    st["boots"] = int(st.get("boots") or 0) + 1
    save(st)
    prev = kernel_log("-1")
    if prev and KARG in prev and gpu_errors(prev):
        line = gpu_errors(prev)[0][:120]
        ok, msg = turn_off(st, "errores de GPU en el arranque anterior: " + line)
        print(f"stone-cu40: GPU errors in the previous 40 CU boot ({line}); 40 CU turned off: "
              f"{'ok' if ok else msg}", file=sys.stderr)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "enable":
        return cmd_enable()
    if cmd == "disable":
        return cmd_disable()
    if cmd == "test":
        return cmd_test(argv[1:])
    if cmd == "guard":
        return cmd_guard()
    say(__doc__.split("\n\n")[1])
    return 1


if __name__ == "__main__":
    sys.exit(main())
