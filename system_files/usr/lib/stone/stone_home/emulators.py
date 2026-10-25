"""Emulators for other consoles, installed from Flathub and added to Steam as shortcuts.

A fixed catalog (Flathub ids checked against Flathub's Emulator category, 25-10-2026). Nothing
else can be installed from STONE Home: the page sends a catalog key, never a package name.

  install    `flatpak install --user flathub <id>` (no root, no password; the user's own
             Flatpak installation), then the emulator is added to Steam as a non-Steam
             shortcut so it appears in STONE's library and launches like a game:
             while Steam runs, through Steam's interface (steamcef.add_shortcut); otherwise
             it is queued and written by `stone-home-session register` before Steam starts.
  installed  which catalog entries are installed (user or system Flatpak).

Games (ROMs) and BIOS files are the person's own; STONE does not download them.
"""

import json
import os
import subprocess

from . import shortcuts as SC

FLATPAK = "/usr/bin/flatpak"
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"

# key -> (Flathub id, name shown, what it plays)
CATALOG = {
    "retrodeck": ("net.retrodeck.retrodeck", "RetroDECK", "Todo en uno: la mayoría de consolas clásicas en una sola app, pensada para mando"),
    "retroarch": ("org.libretro.RetroArch", "RetroArch", "Consolas clásicas con núcleos de libretro"),
    "dolphin": ("org.DolphinEmu.dolphin-emu", "Dolphin", "GameCube y Wii"),
    "pcsx2": ("net.pcsx2.PCSX2", "PCSX2", "PlayStation 2"),
    "ppsspp": ("org.ppsspp.PPSSPP", "PPSSPP", "PlayStation Portable"),
    "rpcs3": ("net.rpcs3.RPCS3", "RPCS3", "PlayStation 3"),
    "cemu": ("info.cemu.Cemu", "Cemu", "Wii U"),
    "azahar": ("org.azahar_emu.Azahar", "Azahar", "Nintendo 3DS"),
    "melonds": ("net.kuribo64.melonDS", "melonDS", "Nintendo DS"),
    "mgba": ("io.mgba.mGBA", "mGBA", "Game Boy Advance"),
    "flycast": ("org.flycast.Flycast", "Flycast", "Dreamcast"),
    "xemu": ("app.xemu.xemu", "xemu", "Xbox original"),
}

PENDING = "pending-shortcuts.json"


def _run(args, timeout):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def installed_ids(run=_run):
    out = set()
    for scope in ("--user", "--system"):
        try:
            r = run([FLATPAK, "list", scope, "--app", "--columns=application"], 30)
        except (OSError, subprocess.SubprocessError):
            continue
        out.update(x.strip() for x in r.stdout.splitlines() if x.strip())
    return out


def catalog(run=_run):
    ids = installed_ids(run)
    return [{"key": k, "name": n, "plays": p, "installed": fid in ids} for k, (fid, n, p) in CATALOG.items()]


def _shortcut_fields(key):
    fid, name, _ = CATALOG[key]
    return name, FLATPAK, "/", f"run {fid}"


def queue_shortcut(key):
    """Remember a shortcut to add the next time Steam is closed (register, before Steam starts)."""
    path = os.path.join(SC.state_dir(), PENDING)
    try:
        with open(path) as f:
            keys = [k for k in json.load(f) if k in CATALOG]
    except (OSError, ValueError, TypeError):
        keys = []
    if key not in keys:
        keys.append(key)
    os.makedirs(SC.state_dir(), exist_ok=True)
    SC._write_atomic(path, (json.dumps(keys) + "\n").encode())


def take_pending():
    path = os.path.join(SC.state_dir(), PENDING)
    try:
        with open(path) as f:
            keys = [k for k in json.load(f) if k in CATALOG]
    except (OSError, ValueError, TypeError):
        return []
    try:
        os.remove(path)
    except OSError:
        pass
    return keys


def pending_entries():
    """Shortcut entries for register(): written into shortcuts.vdf while Steam is closed."""
    out = []
    for key in take_pending():
        name, exe, start, opts = _shortcut_fields(key)
        out.append({"name": name, "exe": f'"{exe}"', "start": f'"{start}"', "options": opts})
    return out


def install(key, progress, run=_run, steam_ui=None):
    """Install one catalog entry and add it to Steam. progress(state, text) is called from this
    thread with state in installing|done|error. Returns True on success."""
    if key not in CATALOG:
        progress("error", "Emulador desconocido")
        return False
    fid, name, _ = CATALOG[key]
    progress("installing", f"Descargando {name} desde Flathub…")
    try:
        run([FLATPAK, "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO], 120)
        r = run([FLATPAK, "install", "--user", "-y", "--noninteractive", "flathub", fid], 3600)
    except (OSError, subprocess.SubprocessError) as e:
        progress("error", f"No se pudo instalar {name}: {e}")
        return False
    if r.returncode != 0 and fid not in installed_ids(run):
        last = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["sin detalle"]
        progress("error", f"No se pudo instalar {name}: {last[0][:160]}")
        return False
    if steam_ui is None:
        from . import steamcef as steam_ui
    if steam_ui.add_shortcut(*_shortcut_fields(key)) is not None:
        progress("done", f"{name} instalado. Ya está en tu biblioteca")
    else:
        queue_shortcut(key)
        progress("done", f"{name} instalado. Aparece en tu biblioteca al reiniciar la consola")
    return True
