"""The real STONE Home Chromium shell (stone_home/chromium.py) against a real stoned.

Starts a private D-Bus, the fake governor and stoned (as in run_tests.py), a fixture Steam
folder, then runs the Chromium shell with `--selftest` under a virtual X server at 3840 x 2160.
The page checks, over the shell's token-protected local HTTP transport, that it reaches stoned,
the library, the settings file, the release file and its fonts, and reports back. Also checked:
the device scale factor for 4K (2), that the page lays out at 1920 x 1080 and draws 3840 x 2160,
and that the shell refuses requests without the token.

    python3 tests/chromium_selftest.py   (needs xvfb-run and a Chromium: STONE_CHROMIUM, or
                                          /usr/bin/chromium-browser, or Playwright's)
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
LIB = os.path.join(REPO, "system_files", "usr", "lib", "stone")

import run_tests as RT  # noqa: E402
import steam_fixture as FX  # noqa: E402


def chromium_binary():
    for p in [os.environ.get("STONE_CHROMIUM"), "/usr/bin/chromium-browser", "/usr/bin/chromium",
              *sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")),
              *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))]:
        if p and os.access(p, os.X_OK):
            return p
    return None


def main():
    binary = chromium_binary()
    if not shutil.which("xvfb-run") or not binary:
        print("SKIP  chromium self-test: xvfb-run or Chromium not installed")
        return 1 if os.environ.get("STONE_REQUIRE_ALL") == "1" else 0
    sys.path.insert(0, LIB)
    from stone_home import chromium as C
    assert C.scale_factor((3840, 2160)) == 2 and C.scale_factor((1920, 1080)) == 1, "scale factor"
    assert C.scale_factor((1280, 800)) == round(1280 / 1920, 4) and C.scale_factor(None) is None
    args = C.build_args("/x/chromium", "http://127.0.0.1:1/s/t/", "/p", 2)
    for must in ("--kiosk", "--app=http://127.0.0.1:1/s/t/", "--force-device-scale-factor=2", "--proxy-server=socks5://127.0.0.1:9",
                 "--autoplay-policy=no-user-gesture-required", "--disable-extensions", "--user-data-dir=/p"):
        assert must in args, must
    env = RT.Env()
    tmp = tempfile.mkdtemp(prefix="stone-chromium-")
    try:
        RT.R.recover(env.store())
        env.start_stoned()
        root = FX.build(os.path.join(tmp, "Steam"))
        rel = os.path.join(tmp, "stone-release")
        with open(rel, "w") as f:
            f.write('STONE_VERSION="test"\n')
        e = dict(env.env, STONE_STEAM_ROOT=root, STONE_HOME_UI=os.path.join(REPO, "system_files/usr/share/stone-home"),
                 STONE_RELEASE_FILE=rel, XDG_CONFIG_HOME=os.path.join(tmp, "config"), XDG_STATE_HOME=os.path.join(tmp, "state"),
                 HOME=tmp, STONE_CHROMIUM=binary, GDK_BACKEND="x11", PYTHONPATH=LIB,
                 # test machines run as root and without a GPU; the console runs sandboxed on its GPU
                 STONE_CHROMIUM_EXTRA="--no-sandbox --use-angle=swiftshader --enable-unsafe-swiftshader")
        code = ("import sys; sys.path.insert(0, %r); from stone_home import chromium; "
                "sys.exit(chromium.main(['stone-home', '--selftest']))" % LIB)
        p = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 3840x2160x24", sys.executable, "-c", code],
                           env=e, capture_output=True, text=True, timeout=240)
        line = next((x for x in p.stdout.splitlines() if x.startswith('{"selftest"')), None)
        result = json.loads(line)["selftest"] if line else None
        print(json.dumps(result, ensure_ascii=False, indent=1))
        log = p.stderr + p.stdout
        if p.returncode != 0 or not result or not result.get("pass"):
            print("FAIL  chromium self-test", p.returncode)
            print(log[-4000:])
            return 1
        if result["checks"].get("library") != 4:
            print("FAIL  chromium self-test: expected 4 games from the fixture")
            return 1
        print("\n".join(x for x in log.splitlines() if "[stone]" in x)[:3000])
        if "screen=3840x2160 scale=2" not in log:
            print("FAIL  chromium self-test: the shell did not see the 4K screen\n" + log[-2000:])
            return 1
        if "[stone] draw: 3840x2160 dpr=2" not in log:
            print("FAIL  chromium self-test: the page was not drawn at 3840x2160\n" + log[-3000:])
            return 1
        print("PASS  chromium self-test (Chromium shell, HTTP transport with token, real stoned, drawn at 3840x2160)")
        return 0
    finally:
        env.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
