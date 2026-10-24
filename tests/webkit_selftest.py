"""The real STONE Home shell (GTK 4 + WebKitGTK 6, stone_home/app.py) against a real stoned.

Starts a private D-Bus, the fake governor and stoned (as in run_tests.py), a fixture Steam
folder, then runs `stone-home --selftest` under a virtual X server. The page checks, through
the WebKit message handler, that it reaches stoned, the library, the settings file, the
release file and its bundled fonts, and reports back; the process exit code is the result.

    python3 tests/webkit_selftest.py        (needs xvfb-run, WebKitGTK 6.0 and GTK 4 typelibs)
"""

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


def main():
    if not shutil.which("xvfb-run"):
        print("SKIP  webkit self-test: xvfb-run not installed")
        return 0
    env = RT.Env()
    tmp = tempfile.mkdtemp(prefix="stone-webkit-")
    try:
        RT.R.recover(env.store())
        env.start_stoned()
        root = FX.build(os.path.join(tmp, "Steam"))
        rel = os.path.join(tmp, "stone-release")
        with open(rel, "w") as f:
            f.write('STONE_VERSION="test"\n')
        e = dict(env.env, STONE_STEAM_ROOT=root, STONE_HOME_UI=os.path.join(REPO, "system_files/usr/share/stone-home"),
                 STONE_RELEASE_FILE=rel, XDG_CONFIG_HOME=os.path.join(tmp, "config"), HOME=tmp,
                 # test machines (root here, AppArmor-restricted user namespaces on CI runners) cannot
                 # start WebKit's bubblewrap sandbox; the console runs with it
                 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS="1",
                 LIBGL_ALWAYS_SOFTWARE="1", GDK_BACKEND="x11", PYTHONPATH=LIB)
        code = ("import sys; sys.path.insert(0, %r); from stone_home.app import main; sys.exit(main(['stone-home', '--selftest']))" % LIB)
        p = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", sys.executable, "-c", code],
                           env=e, capture_output=True, text=True, timeout=180)
        line = next((x for x in p.stdout.splitlines() if x.startswith('{"selftest"')), None)
        result = json.loads(line)["selftest"] if line else None
        print(json.dumps(result, ensure_ascii=False, indent=1))
        if p.returncode != 0 or not result or not result.get("pass"):
            print("FAIL  webkit self-test", p.returncode)
            print(p.stderr[-3000:])
            return 1
        if result["checks"].get("library") != 4:
            print("FAIL  webkit self-test: expected 4 games from the fixture")
            return 1
        print("PASS  webkit self-test (stone-home shell, WebKit transport, real stoned)")
        return 0
    finally:
        env.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
