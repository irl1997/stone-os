"""stonectl: STONE performance from a terminal (desktop mode, SSH or a TTY).

For when the interface cannot be used. Commands:

  stonectl status
  stonectl safe-mode on|off
  stonectl restore-defaults

It asks stoned over D-Bus first. If stoned does not answer, or polkit refuses (a remote
session), and stonectl runs as root, it leaves a request in /var/lib/stone/flags that
`stoned --recover` applies at the next boot. It never touches the GPU itself.
"""

import json
import os
import pwd
import sys

from . import state as S

NAME = "org.stonemachine.Performance1"
PATH = "/org/stonemachine/Performance1"
USAGE = "uso: stonectl status | safe-mode on|off | restore-defaults"

TEXT = {
    "failures": "dos arranques seguidos sin terminar bien",
    "kernel": "pedido en el arranque (stone.safe=1)",
    "manual": "activado por el usuario",
}


def _bus():
    from gi.repository import Gio
    kind = Gio.BusType.SESSION if os.environ.get("STONE_BUS") == "session" else Gio.BusType.SYSTEM
    return Gio.bus_get_sync(kind, None)


def _call(method):
    from gi.repository import Gio, GLib
    bus = _bus()
    try:
        r = bus.call_sync(NAME, PATH, NAME, method, None, GLib.VariantType("(a{sv})"),
                          Gio.DBusCallFlags.NONE, 10000, None)
        return r.unpack()[0], None
    except GLib.Error as e:
        return None, Gio.DBusError.get_remote_error(e) or e.message


def _offline(flag):
    """Leave a request for the next boot. Root only: the state directory is 0700 stoned."""
    if os.geteuid() != 0 and os.environ.get("STONE_CTL_ALLOW_USER") != "1":   # test harness only
        return False
    store = S.Store()
    store.set_flag(flag, {"by": "stonectl"})
    try:
        pw = pwd.getpwnam("stoned")
        os.chown(os.path.join(store.flags, flag), pw.pw_uid, pw.pw_gid)
    except (KeyError, PermissionError):
        pass
    return True


def status(out):
    st, err = _call("GetState")
    if st is None:
        out.write(f"stoned no responde ({err}).\n")
        out.write("La GPU sigue con la configuración de fábrica del regulador.\n")
        return 1
    a = st.get("actual", {})
    lines = [
        f"Perfil: {st['profile']}" + (" (provisional)" if st.get("provisional") else ""),
        f"Safe Mode: {'sí — ' + TEXT.get(st.get('safe_reason'), '') if st['safe_mode'] else 'no'}",
        f"Regulador: GPU {a['min']}–{a['max']} MHz, límite {a['temp']} °C" if "max" in a else f"Regulador: no disponible ({a.get('error')})",
        f"Fallos seguidos: {st['fail_count']}",
    ]
    th = st.get("thermal") or {}
    if th.get("reduced"):
        lines.append("Temperatura: rendimiento reducido a SILENT")
    out.write("\n".join(lines) + "\n")
    return 0


def safe_mode(on, out):
    st, err = _call("EnterSafeMode" if on else "ExitSafeMode")
    if st is not None:
        out.write("Safe Mode activado.\n" if on else "Safe Mode desactivado.\n")
        return 0
    if err and err.endswith("SafeMode") and not on:
        out.write("Safe Mode está pedido en el arranque (stone.safe=1). Quítalo de los parámetros de arranque.\n")
        return 1
    if _offline(S.FLAG_SAFE_REQUEST if on else S.FLAG_SAFE_EXIT):
        out.write(f"stoned no respondió ({err}). Se aplicará en el próximo arranque.\n")
        return 0
    out.write(f"No se pudo ({err}). Ejecútalo con sudo para dejarlo pedido para el próximo arranque.\n")
    return 1


def restore_defaults(out):
    st, err = _call("RestoreDefaults")
    if st is not None:
        out.write("Valores de fábrica restaurados: BALANCED, sin perfiles por juego, fuera de Safe Mode.\n")
        return 0
    if _offline(S.FLAG_SAFE_EXIT):
        out.write(f"stoned no respondió ({err}). En el próximo arranque se sale de Safe Mode; "
                  "la GPU ya arranca siempre con la configuración de fábrica.\n")
        return 0
    out.write(f"No se pudo ({err}). Ejecútalo con sudo.\n")
    return 1


def main(argv, out=sys.stdout):
    if argv == ["status"]:
        return status(out)
    if len(argv) == 2 and argv[0] == "safe-mode" and argv[1] in ("on", "off"):
        return safe_mode(argv[1] == "on", out)
    if argv == ["restore-defaults"]:
        return restore_defaults(out)
    if argv == ["--json", "status"]:
        st, err = _call("GetState")
        out.write(json.dumps(st if st is not None else {"error": err}, default=str) + "\n")
        return 0 if st is not None else 1
    out.write(USAGE + "\n")
    return 2
