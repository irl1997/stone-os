"""stonectl display: the STONE DISPLAY diagnostic.

  stonectl display                 what is connected, native and output modes, game environment
  stonectl display --json          the same model as JSON
  stonectl display probe           asks every page of Steam's interface what it offers (names only) and
                                   whether it answers the display state; saves ~/.local/state/stone/steam-api.json
  stonectl display set-output WxH  EXPERIMENTAL, only a mode this connection can carry: asks Game Mode for that
                                   output size at the next start (~/.config/environment.d/90-stone-display.conf)
  stonectl display reset           removes that file: Game Mode chooses again
  stonectl display goal fluid|max  fluid (default): the highest resolution at 60 Hz or more; max: the highest
                                   resolution at any refresh (4K30 on a link that cannot do 4K60)
  stonectl display diagnostics     everything above plus the Gamescope command line, Gamescope's
                                   saved modes, session overrides and ScopeBuddy files, saved
                                   to ~/.local/state/stone/display-diagnostics.txt for support

Run as the console user from a terminal on the console (Ctrl+Alt+F3, or Konsole), not as root:
Steam's display state is read from the user's Steam. No serial numbers, no network data.
"""

import glob
import json
import os
import re
import sys
import time

from . import _r, collect, link, state_dir

ENV_FILE = "~/.config/environment.d/90-stone-display.conf"

INTERESTING_ENV = re.compile(r"^\s*(export\s+)?(SCREEN_WIDTH|SCREEN_HEIGHT|INTERNAL_WIDTH|INTERNAL_HEIGHT|OUTPUT_CONNECTOR|"
                             r"ADAPTIVE_SYNC|ENABLE_GAMESCOPE_HDR|CUSTOM_REFRESH_RATES|DRM_MODE|GAMESCOPECMD|MAX_SCALE)=", re.MULTILINE)
INTERESTING_SCB = re.compile(r"^\s*(export\s+)?(SCB_[A-Z_]+|GAMESCOPE[A-Z_]*)=.*$", re.MULTILINE)


def _home():
    return os.path.expanduser("~")


def gamescope_cmdlines():
    """The running Gamescope's arguments (-W/-H output size, -w/-h game size, ...), as evidence."""
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            with open(os.path.join(d, "cmdline"), "rb") as f:
                args = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if args and os.path.basename(args[0]).startswith("gamescope") and not os.path.basename(args[0]).startswith("gamescope-"):
            out.append(" ".join(args))
    return sorted(set(out))


def session_overrides():
    files = []
    for pat in ("~/.config/environment.d/*.conf", "~/.config/gamescope-session-plus/sessions.d/*",
                "/etc/environment.d/*.conf", "/usr/share/gamescope-session-plus/sessions.d/*",
                "/etc/gamescope-session-plus/sessions.d/*"):
        files += glob.glob(os.path.expanduser(pat))
    out = {}
    for p in sorted(set(files)):
        try:
            with open(p) as f:
                text = f.read()
                lines = [text[m.start():].split("\n", 1)[0].strip() for m in INTERESTING_ENV.finditer(text)]
        except (OSError, UnicodeDecodeError):
            continue
        if lines:
            out[p.replace(_home(), "~")] = lines
    return out


def scopebuddy():
    out = {}
    for p in sorted(glob.glob(os.path.expanduser("~/.config/scopebuddy/**/*.conf"), recursive=True)):
        try:
            with open(p) as f:
                lines = [m.group(0).strip() for m in INTERESTING_SCB.finditer(f.read())]
        except (OSError, UnicodeDecodeError):
            continue
        out[p.replace(_home(), "~")] = lines
    return out


def saved_modes():
    p = os.environ.get("GAMESCOPE_MODE_SAVE_FILE") or os.path.expanduser("~/.config/gamescope/modes.cfg")
    try:
        with open(p) as f:
            return [x.strip() for x in f.read().splitlines() if x.strip()][:20]
    except OSError:
        return None


def status_text(snap):
    p = snap.get("primary")
    L = ["STONE DISPLAY", ""]
    if not p:
        L += ["Pantalla:            ninguna conectada", ""]
    else:
        L += [f"Pantalla:            {p.get('display_name') or 'UNKNOWN'} ({p.get('maker') or '?'})",
              f"Conexión:            {p.get('connection_type')} · {p['connector']}"
              + (f" · HDMI hasta {p['hdmi_max_tmds_mhz']} MHz TMDS" if p.get("hdmi_max_tmds_mhz") else "")
              + (f" · FRL {p['hdmi_frl_gbps']} Gbps" if p.get("hdmi_frl_gbps") else ""),
              f"A · Nativa:          {_r(p.get('native_resolution'))}",
              (f"    Frecuencias:     {', '.join(str(x) + ' Hz' for x in p.get('refresh_rates') or []) or 'UNKNOWN'} a la nativa"
               f" ({p.get('modes_source') or 'sin datos'})"),
              "    VRR:             " + (f"sí, {p['vrr_range']['min']}–{p['vrr_range']['max']} Hz ({p['vrr_range']['source']})"
                                        if p.get("vrr_range") else {True: "sí", False: "no", None: "UNKNOWN"}[p.get("vrr_supported")])
              + ("" if p.get("vrr_active") is None else (" · activo" if p["vrr_active"] else " · inactivo")),
              "    HDR:             " + {True: "compatible", False: "no", None: "UNKNOWN"}[p.get("hdr_supported")]
              + ("" if p.get("hdr_active") is None else (" · activo" if p["hdr_active"] else " · apagado")),
              f"B · Salida actual:   {_r(snap.get('output'))}" + ("" if snap.get("steam_available") else "  (Steam no respondió)"),
              f"    Modo preferido:  {_r(snap.get('preferred_mode'))}",
              f"C · Juegos ven:      {_r(snap.get('game_environment'))} (política: "
              + ("nativa" if snap.get("game_policy") == "native" else "la de Steam") + ")",
              f"    Steam por defecto: {_r(snap.get('steam_game_default'))}",
              "D · Interna:         la elige cada juego (STONE GRAPHICS, fase siguiente)",
              f"E · Final:           {_r(snap.get('final_output'))}"]
        lk = snap.get("link")
        if lk:
            L.append(f"    Enlace:          {lk['budget_source']}" + (f" (≈{lk['budget_mhz']} MHz)" if lk.get("budget_mhz") else ""))
            L.append(f"    Mejor fluida:    {_r(lk.get('best_fluid'))}   Máxima resolución: {_r(lk.get('best_max_res'))}"
                     f"   Meta: {'máxima resolución' if lk.get('goal') == 'max_res' else 'fluidez (≥60 Hz)'}")
            L.append("    4K a 60 Hz:      " + ("sí" if lk.get("reaches_4k60") else
                                               "NO con esta conexión" if lk.get("display_offers_4k60") else "la pantalla no lo ofrece"))
            if lk.get("unreachable"):
                L.append("    No caben:        " + ", ".join(_r(m) + f" ({m['need_mhz']} MHz)" for m in lk["unreachable"][:4]))
        if snap.get("steam_error"):
            L.append(f"    Steam:           NO entregó el estado ({str(snap['steam_error'])[:200]}). Prueba: stonectl display probe")
        xw = snap.get("xwayland") or {}
        if xw:
            L.append("    Xwayland:        " + "  ".join(f"{k} {_r(v)}" for k, v in sorted(xw.items())))
        if snap.get("safe_display_mode"):
            L.append("    MODO SEGURO:     no se fuerza nada; se deja la salida que eligió Gamescope")
    others = [d for d in snap["displays"] if d is not p]
    if others:
        L += ["", "Otras salidas: " + ", ".join(f"{d['connector']} ({'conectada' if d['connected'] else 'desconectada'})" for d in others)]
    f = [x for x in snap.get("findings", [])]
    if f:
        L += ["", "Diagnóstico:"] + [f"  [{x['level'].upper()}] {x['code']}: {x['text']}" for x in f]
    return "\n".join(L) + "\n"


def _count(tree):
    return sum(1 if isinstance(v, str) else _count(v) for v in tree.values())


def _find(tree, *path):
    for k in path:
        tree = tree.get(k) if isinstance(tree, dict) else None
    return tree


def probe(out):
    """What each Steam interface page offers; the answer decides how STONE talks to Steam."""
    from stone_home import steamcef
    from . import steam
    rows = steamcef.probe()
    api = {"time": int(time.time()), "pages": []}
    out.write("STONE ↔ STEAM (interfaz de Steam)\n\n")
    if not rows:
        out.write("Steam no respondió en 127.0.0.1:8080. ¿Estás en Game Mode con Steam abierto?\n")
    for r in rows:
        if r.get("error"):
            out.write(f"  página {r['title']!r}: ERROR {r['error']}\n")
            continue
        tree = r.get("tree") or {}
        dm = _find(tree, "System", "DisplayManager")
        n = _count(tree) if tree else 0
        out.write(f"  página {r['title']!r}: SteamClient {'SÍ' if r.get('has_steamclient') else 'no'}, {n} funciones"
                  + (f", DisplayManager: {', '.join(sorted(dm))}" if isinstance(dm, dict) else ", sin DisplayManager") + "\n")
        api["pages"].append({"title": r["title"], "has_steamclient": r.get("has_steamclient"), "tree": tree})
    st, err = steam.display_state()
    out.write("\nEstado de pantalla de Steam: " + (f"LEÍDO ({len(st['displays'])} pantalla(s), juegos por defecto {_r(st.get('game_default'))})"
                                                     if st else f"NO leído: {err}") + "\n")
    path = os.path.join(state_dir(), "steam-api.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(api, f)
    out.write(f"\nLista de funciones (solo nombres) guardada en {path.replace(_home(), '~')}\n")
    return 0


def set_output(arg, snap, out):
    import re as _re
    m = _re.fullmatch(r"(\d{3,4})x(\d{3,4})", arg or "")
    if not m:
        out.write("uso: stonectl display set-output 2560x1440\n")
        return 2
    w, h = int(m.group(1)), int(m.group(2))
    lk = snap.get("link")
    p = snap.get("primary")
    if not p or not lk or not lk.get("best_max_res"):
        out.write("No hay datos de la conexión: STONE no fuerza una resolución sin saber qué admite. No se cambió nada.\n")
        return 1
    offered = {(x["w"], x["h"]) for x in p.get("available_modes") or []}
    if (w, h) not in offered or not link.reachable(lk, w, h):
        out.write(f"{w}x{h} no es un modo que la pantalla ofrezca y esta conexión pueda llevar. "
                  f"Lo máximo es {_r(lk['best_max_res'])}. No se cambió nada.\n")
        return 1
    path = os.path.expanduser(ENV_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"# STONE DISPLAY: tamaño de salida de Game Mode. Se quita con: stonectl display reset\n"
                f"SCREEN_WIDTH={w}\nSCREEN_HEIGHT={h}\n")
    out.write(f"Escrito {ENV_FILE}: Game Mode pedirá {w}x{h} al próximo inicio (reinicia la consola).\n"
              "EXPERIMENTAL, sin verificar en hardware. Si la pantalla queda negra: Ctrl+Alt+F3, entra y escribe «stonectl display reset» y reinicia.\n")
    return 0


def main(argv, out=sys.stdout):
    from stone_home import system as S
    prefs = S.load_prefs()
    pol = "native" if (prefs.get("game_res", 0) == 0) else "steam"
    if argv == ["probe"]:
        return probe(out)
    if argv == ["reset"]:
        path = os.path.expanduser(ENV_FILE)
        if os.path.exists(path):
            os.remove(path)
            out.write("Quitado. Game Mode elegirá la salida solo al próximo inicio (reinicia la consola).\n")
        else:
            out.write("No había ningún cambio de STONE.\n")
        return 0
    if len(argv) == 2 and argv[0] == "goal":
        if argv[1] not in ("fluid", "max"):
            out.write("uso: stonectl display goal fluid|max\n")
            return 2
        prefs["display_goal"] = "max_res" if argv[1] == "max" else "fluid"
        S.save_prefs(prefs)
        out.write("Meta guardada: " + ("máxima resolución (aunque sean menos Hz)" if argv[1] == "max" else "fluidez (≥60 Hz)") + "\n")
        return 0
    snap = collect(pol, goal=prefs.get("display_goal", "fluid"))
    if len(argv) == 2 and argv[0] == "set-output":
        return set_output(argv[1], snap, out)
    if argv == ["--json"]:
        out.write(json.dumps(snap, indent=1, default=str) + "\n")
        return 0
    if argv in ([], ["status"]):
        out.write(status_text(snap))
        return 0
    if argv == ["diagnostics"]:
        text = status_text(snap) + "\n" + "\n".join([
            "Gamescope (línea de órdenes):", *(["  " + c for c in gamescope_cmdlines()] or ["  no está corriendo"]),
            "", "Modos guardados por Gamescope (" + (os.environ.get("GAMESCOPE_MODE_SAVE_FILE") or "~/.config/gamescope/modes.cfg") + "):",
            *(["  " + x for x in (saved_modes() or [])] or ["  ninguno"]),
            "", "Ajustes de sesión (resolución, salida, VRR, HDR):",
            *([f"  {k}: {v}" for k, v in session_overrides().items()] or ["  ninguno"]),
            "", "ScopeBuddy:", *([f"  {k}: {v}" for k, v in scopebuddy().items()] or ["  sin archivos (no se usa)"]),
            "", "Modelo completo (JSON):", json.dumps({k: v for k, v in snap.items() if k != "displays"} | {
                "displays": [{k: v for k, v in d.items() if k != "hdr"} for d in snap["displays"]]}, indent=1, default=str)])
        path = os.path.join(state_dir(), "display-diagnostics.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(f"# STONE display diagnostics {time.strftime('%Y-%m-%d %H:%M:%S')}\n" + text + "\n")
        out.write(status_text(snap) + f"\nGuardado completo en {path.replace(_home(), '~')}\n")
        return 0
    out.write("uso: stonectl display [--json | probe | diagnostics | set-output WxH | reset | goal fluid|max]\n")
    return 2
