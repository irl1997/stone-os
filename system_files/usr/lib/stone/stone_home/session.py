"""`stone-home-session`: STONE Home's place in Steam Game Mode.

  register   add/update the STONE shortcut and STONE's startup movie (only while Steam is not running)
  launch     as soon as Steam's interface is up, open STONE Home through Steam and confirm it runs
  status     print what is registered, as JSON
  diag       everything needed to diagnose STONE Home on the console, in one screen

Both are run by systemd user units bound to the Game Mode session. A failure is logged
and leaves Steam's own Home on screen; it never blocks the session.
"""

import json
import os
import subprocess
import sys
import time

from . import shortcuts as SC
from . import steamcef


def _env_float(name, default, lo=0.0, hi=60.0):
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return default
    return min(hi, max(lo, v))


# The session runs Steam on its own X display (DISPLAY=:0 under Gamescope), which user units
# and the console do not have (gamescope-session-plus unsets DISPLAY in the user manager).
STEAM_ENV_KEYS = ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "GAMESCOPE_WAYLAND_DISPLAY",
                  "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP")


def _steam_env(proc="/proc"):
    """The running Steam client's display variables, merged over our own environment."""
    env = dict(os.environ)
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return env
    for p in pids:
        try:
            with open(os.path.join(proc, p, "comm")) as f:
                if f.read().strip() != "steam":
                    continue
            with open(os.path.join(proc, p, "environ"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        for item in raw.split(b"\0"):
            k, sep, v = item.partition(b"=")
            key = k.decode(errors="replace")
            if sep and key in STEAM_ENV_KEYS:
                env[key] = v.decode(errors="replace")
        break
    return env


def _ask_steam(url, env):
    """`steam <url>` hands the URL to the running client and exits; waited for so the unit
    does not end (and get its cgroup cleaned up) with the request in flight. What Steam's
    launcher prints goes to the journal."""
    try:
        r = subprocess.run(["/usr/bin/steam", url], stdin=subprocess.DEVNULL, env=env,
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout + r.stderr).strip()
        if out:
            print(f"stone-home-session: steam said: {out[-300:]}", flush=True)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"stone-home-session: could not ask Steam to open STONE Home: {e}", flush=True)


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "register":
        print(json.dumps(SC.register()), flush=True)
        return 0
    if cmd == "launch":
        if os.environ.get("STONE_HOME_AUTOSTART", "1") == "0":
            print("stone-home-session: autostart disabled (STONE_HOME_AUTOSTART=0)", flush=True)
            return 0
        if not SC.is_registered():
            print("stone-home-session: STONE shortcut not registered yet; it will be on the next start", flush=True)
            return 0
        settle = _env_float("STONE_HOME_SETTLE_S", 2.0)
        retry = _env_float("STONE_HOME_RETRY_S", 3.0)
        waited = SC.wait_for_steam(settle_s=settle)
        if waited is None:
            print("stone-home-session: Steam did not come up; not launching STONE Home", flush=True)
            return 0
        urls = SC.launch_urls()
        env = _steam_env()
        print(f"stone-home-session: DISPLAY={env.get('DISPLAY', '-')} trying {', '.join(urls)}", flush=True)
        sent = []
        last = {"cef": None}
        use_cef = os.environ.get("STONE_HOME_CEF", "1") != "0"
        gids = SC.candidate_game_ids()

        def request():
            # A start that Steam accepted gets 12 s to bring the window up before asking again.
            if last.get("ok_at") and time.monotonic() - last["ok_at"] < 12:
                return
            # First through Steam's interface (SteamClient.Apps.RunGame, as Decky Loader does):
            # in Game Mode Steam receives steam://rungameid for a non-Steam shortcut and does
            # nothing (board, RC3). The URL stays as the fallback while that is not reachable.
            if use_cef and gids:
                st = steamcef.run_game(gids[len(sent) % len(gids)])
                if st != last["cef"]:
                    print(f"stone-home-session: {st}", flush=True)
                    last["cef"] = st
                if st.endswith(": ok"):
                    last["ok_at"] = time.monotonic()
                    sent.append(f"SteamClient.Apps.RunGame({gids[len(sent) % len(gids)]})")
                    return
            url = urls[len(sent) % len(urls)]
            sent.append(url)
            _ask_steam(url, env)
        res = SC.launch_until_running(request, retry_s=retry, timeout_s=_env_float("STONE_HOME_TIMEOUT_S", 300, 10, 600))
        if res["ok"] and sent:
            print(f"stone-home-session: opened with {sent[-1]}", flush=True)
        total = round(waited + res["seconds"], 2)
        # One line per boot with the timings, to tune STONE_HOME_SETTLE_S / STONE_HOME_RETRY_S
        # on the board (HARDWARE_VALIDATION.md, test 16): journalctl --user -u stone-home-autostart
        print(f"stone-home-session: {'STONE Home running' if res['ok'] else 'STONE Home did not start'} "
              f"steam_ui_after={round(waited, 2)}s requests={res['requests']} home_after={total}s "
              f"(settle={settle}s retry={retry}s)", flush=True)
        return 0
    if cmd == "diag":
        return _diag()
    if cmd == "status":
        movie = os.path.join(SC.steam_root(), "config", "uioverrides", "movies", SC.BOOT_VIDEO_NAME)
        print(json.dumps({"registered": SC.is_registered(), "appid": SC.short_appid(), "urls": SC.launch_urls(),
                          "steam_ids": SC.recorded_ids(), "vdf_appids": SC.shortcut_appids(),
                          "steam_root": SC.steam_root(), "startup_movie": os.path.exists(movie),
                          "startup_movie_is_stone": os.path.exists(movie + ".stone"),
                          "home_running": SC.home_running()}), flush=True)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


def _tail(path, n, keep=None):
    try:
        with open(path, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ["(" + path + ": not found)"]
    if keep:
        lines = [x for x in lines if any(k in x for k in keep)]
    return lines[-n:]


def _diag():
    """One screen for a photo: version, shortcut ids, last boot's autostart, rendering and audio."""
    out = []
    rel = {}
    try:
        with open("/usr/lib/stone-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                rel[k] = v.strip('"')
    except OSError:
        pass
    out.append(f"STONE {rel.get('STONE_VERSION', '?')}  WebKitGTK {rel.get('STONE_WEBKITGTK', '?')}  kernel {rel.get('STONE_KERNEL', '?')}")
    out.append(f"registered={SC.is_registered()} vdf_appids={SC.shortcut_appids()} computed={SC.short_appid()}")
    out.append(f"steam_ids={SC.recorded_ids()}  running={SC.home_running()}")
    out.append("urls: " + " ".join(SC.launch_urls()))
    flag = os.path.exists(os.path.join(SC.steam_root(), steamcef.FLAG))
    try:
        pages = [p.get("title", "?") for p in steamcef.targets()][:4]
    except (OSError, ValueError) as e:
        pages = f"unreachable ({e.__class__.__name__})"
    out.append(f"steam ui launch: flag={flag} pages={pages}")
    try:
        j = subprocess.run(["journalctl", "--user", "-b", "-o", "cat", "--no-pager", "-n", "6",
                            "-u", "stone-home-autostart", "-u", "stone-home-register"],
                           capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        j = []
    out.append("-- autostart (this boot)")
    out += [x[:180] for x in j[-6:]] or ["(nothing)"]
    out.append("-- STONE Home log")
    out += [x[:180] for x in _tail(os.path.join(SC.state_dir(), "home.log"), 14,
                                    keep=("[stone]", "stone-home", "rror", "arn", "GStreamer", "Audio", "audio"))]
    out.append("-- Steam log (rungameid)")
    out += [x[:180] for x in _tail(os.path.join(SC.steam_root(), "logs", "console_log.txt"), 4,
                                    keep=("rungameid", "ExecCommandLine", "GameAction"))]
    print("\n".join(out), flush=True)
    return 0
