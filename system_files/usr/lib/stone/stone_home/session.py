"""`stone-home-session`: STONE Home's place in Steam Game Mode.

  register   add/update the STONE shortcut and STONE's startup movie (only while Steam is not running)
  launch     as soon as Steam's interface is up, open STONE Home through Steam and confirm it runs
  status     print what is registered, as JSON

Both are run by systemd user units bound to the Game Mode session. A failure is logged
and leaves Steam's own Home on screen; it never blocks the session.
"""

import json
import os
import subprocess
import sys

from . import shortcuts as SC


def _env_float(name, default, lo=0.0, hi=60.0):
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return default
    return min(hi, max(lo, v))


def _ask_steam():
    """`steam <url>` hands the URL to the running client and exits; waited for so the unit
    does not end (and get its cgroup cleaned up) with the request in flight."""
    try:
        subprocess.run(["/usr/bin/steam", SC.launch_url()], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
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
        res = SC.launch_until_running(_ask_steam, retry_s=retry)
        total = round(waited + res["seconds"], 2)
        # One line per boot with the timings, to tune STONE_HOME_SETTLE_S / STONE_HOME_RETRY_S
        # on the board (HARDWARE_VALIDATION.md, test 16): journalctl --user -u stone-home-autostart
        print(f"stone-home-session: {'STONE Home running' if res['ok'] else 'STONE Home did not start'} "
              f"steam_ui_after={round(waited, 2)}s requests={res['requests']} home_after={total}s "
              f"(settle={settle}s retry={retry}s)", flush=True)
        return 0
    if cmd == "status":
        movie = os.path.join(SC.steam_root(), "config", "uioverrides", "movies", SC.BOOT_VIDEO_NAME)
        print(json.dumps({"registered": SC.is_registered(), "appid": SC.short_appid(), "url": SC.launch_url(),
                          "steam_root": SC.steam_root(), "startup_movie": os.path.exists(movie),
                          "startup_movie_is_stone": os.path.exists(movie + ".stone"),
                          "home_running": SC.home_running()}), flush=True)
        return 0
    print(__doc__, file=sys.stderr)
    return 2
