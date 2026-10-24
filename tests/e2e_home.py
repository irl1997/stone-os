"""STONE Home end-to-end: production page + host (dev server) + real stoned + fake governor.

Starts a private D-Bus, the fake governor and stoned (as in run_tests.py), a fixture Steam
folder and development/stone_home_devserver.py with fake Steam and fake power, then runs
tests/e2e/home.e2e.mjs in headless Chromium (Playwright). Afterwards it checks what reached
the governor, Steam and logind.

    python3 tests/e2e_home.py        (needs node and the playwright package, see tests/e2e/package.json)
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import run_tests as RT  # noqa: E402
import steam_fixture as FX  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    if not shutil.which("node"):
        print("SKIP  e2e: node not installed")
        return 0
    env = RT.Env()
    tmp = tempfile.mkdtemp(prefix="stone-e2e-")
    srv = None
    try:
        RT.R.recover(env.store())
        env.start_stoned()
        root = FX.build(os.path.join(tmp, "Steam"))
        launches, power = os.path.join(tmp, "launches.jsonl"), os.path.join(tmp, "power.jsonl")
        port = free_port()
        e = dict(env.env, XDG_CONFIG_HOME=os.path.join(tmp, "config"), STONE_RELEASE_FILE=os.path.join(tmp, "rel"))
        with open(e["STONE_RELEASE_FILE"], "w") as f:
            f.write('STONE_VERSION="e2e"\n')
        srv = subprocess.Popen([sys.executable, os.path.join(REPO, "development", "stone_home_devserver.py"), "--port", str(port),
                                "--steam-root", root, "--fake-steam", launches, "--fake-power", power], env=e)
        url = f"http://127.0.0.1:{port}/"
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        node_env = dict(e, STONE_HOME_URL=url, STONE_E2E_SHOT=os.path.join(REPO, "e2e-failure.png"),
                        NODE_PATH=os.environ.get("NODE_PATH", os.path.join(HERE, "e2e", "node_modules")))
        p = subprocess.run(["node", os.path.join(HERE, "e2e", "home.e2e.mjs")], env=node_env, timeout=300,
                           cwd=os.path.join(HERE, "e2e"))
        if p.returncode != 0:
            print("FAIL  e2e (browser part)")
            return 1
        gov = env.gov_calls()
        ranges = [c for c in gov if c.get("call") == "SetRange"]
        assert any(c.get("args") == [1000, 2000] for c in ranges), f"PERFORMANCE never reached the governor: {ranges}"
        assert any(c.get("args") == [1000, 1500] for c in ranges), f"per-game SILENT never reached the governor: {ranges}"
        urls = [json.loads(x)["url"] for x in open(launches)]
        assert urls == ["steam://rungameid/1001"], urls
        assert [json.loads(x)["action"] for x in open(power)] == ["off"]
        st = env.call("GetState")
        assert st["profile"] == "performance" and st["per_game"] == {"1001": "silent"} and "game" not in st, st
        print("PASS  e2e: governor received PERFORMANCE and the per-game SILENT; Steam got one launch; logind got one power off")
        return 0
    except AssertionError as ex:
        print(f"FAIL  e2e: {ex}")
        return 1
    finally:
        if srv:
            srv.terminate()
            srv.wait()
        env.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
