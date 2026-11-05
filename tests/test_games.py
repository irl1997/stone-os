"""STONE OPTIMIZED: game profiles, STONE GRAPHICS, metrics, STONE EXPERIENCE SCORE and the
optimizer's launch / exit / crash flows, against a fake Steam (folder + interface), a fake stoned
and fake game processes. Mocks live only in this file, never in production.

    python3 tests/test_games.py [name-filter...]
"""

import json
import os
import random
import re
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUILTIN = os.path.join(REPO, "system_files", "usr", "share", "stone", "game-profiles")
os.environ["STONE_GAME_PROFILES"] = BUILTIN
sys.path.insert(0, os.path.join(REPO, "system_files", "usr", "lib", "stone"))

from stone_games import detect, graphics, metrics, optimizer, schema, score, store  # noqa: E402

TESTS = []
CP = "1091500"


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- the fake world
class World:
    """A Steam folder with Cyberpunk 2077 (and an unknown game), Steam's interface for launch
    options, stoned, the game's process and a clock. Everything in a temporary folder."""

    def __init__(self, injected=None, proxy="winmm", anti_cheat=False, hz=60, vrr=False, display=True,
                 validated=1850, user_options=""):
        self.d = tempfile.mkdtemp(prefix="stone-games-")
        self.old_env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "XDG_DATA_HOME")}
        os.environ["XDG_STATE_HOME"] = os.path.join(self.d, "state")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.d, "data")
        self.root = os.path.join(self.d, "Steam")
        self.game_dir = os.path.join(self.root, "steamapps", "common", "Cyberpunk 2077")
        os.makedirs(os.path.join(self.game_dir, "bin", "x64"))
        self.write_manifest(CP, "Cyberpunk 2077", "Cyberpunk 2077", build="19000000")
        self.write_manifest("4242", "Juego Desconocido", "Juego Desconocido", build="7")
        os.makedirs(os.path.join(self.root, "steamapps", "common", "Juego Desconocido"))
        os.makedirs(os.path.join(self.root, "config"))
        self.set_proton("proton_9")
        if injected:
            self.install(injected, proxy)
        if anti_cheat:
            os.makedirs(os.path.join(self.game_dir, "EasyAntiCheat"))
        self.options = {CP: user_options, "4242": ""}
        self.cef_up = True
        self.set_calls = []
        self.perf = FakePerf(validated)
        self.now = 1_800_000_000.0
        self.hz, self.vrr, self.has_display = hz, vrr, display
        self.launched = []
        self.e = optimizer.Engine(root=lambda: self.root, cef=self, display=self.display, perf=self.perf,
                                  watch=self, clock=lambda: self.now, log=lambda m: None)

    def close(self):
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.d, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # Steam folder
    def write_manifest(self, appid, name, installdir, build):
        p = os.path.join(self.root, "steamapps", f"appmanifest_{appid}.acf")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(f'"AppState"\n{{\n\t"appid"\t"{appid}"\n\t"name"\t"{name}"\n\t"StateFlags"\t"4"\n'
                    f'\t"installdir"\t"{installdir}"\n\t"buildid"\t"{build}"\n}}\n')

    def set_proton(self, name):
        with open(os.path.join(self.root, "config", "config.vdf"), "w") as f:
            f.write(f'"InstallConfigStore"\n{{ "Software" {{ "Valve" {{ "Steam" {{ "CompatToolMapping" {{\n'
                    f'\t"{CP}" {{ "name" "{name}" "config" "" "priority" "250" }}\n}} }} }} }} }}\n')

    def install(self, kind, proxy):
        x64 = os.path.join(self.game_dir, "bin", "x64")
        with open(os.path.join(x64, "OptiScaler.ini"), "w") as f:
            f.write("[Upscalers]\n")
        with open(os.path.join(x64, f"{proxy}.dll"), "wb") as f:
            f.write(b"MZ" + bytes(100))
        if kind == "helixsr":
            os.makedirs(os.path.join(x64, "HelixSR"), exist_ok=True)
            with open(os.path.join(x64, "HelixSR", "helixsr.dll"), "wb") as f:
                f.write(b"MZ" + bytes(200))

    # Steam's interface (cef.ui_value / run_game)
    def ui_value(self, expr):
        if not self.cef_up:
            return None
        m = re.search(r"RegisterForAppDetails\((\d+),", expr)
        if m:
            return json.dumps({"options": self.options.get(m.group(1), "")})
        m = re.search(r"SetAppLaunchOptions\((\d+),(\".*\")\);", expr)
        if m:
            self.options[m.group(1)] = json.loads(m.group(2))
            self.set_calls.append((m.group(1), self.options[m.group(1)]))
            return json.dumps({"ok": True})
        return None

    def owned_games(self):
        return None

    def run_game(self, gid):
        self.launched.append(gid)
        return "cef[SharedJSContext]: ok"

    # STONE DISPLAY
    def display(self):
        if not self.has_display:
            return {"primary": None, "output": None}
        return {"primary": {"native_resolution": {"w": 3840, "h": 2160, "hz": self.hz}, "vrr_supported": self.vrr,
                            "vrr_range": {"min": 48, "max": self.hz} if self.vrr else None, "vrr_active": self.vrr,
                            "hdr_supported": False, "hdr_active": False, "modes": [{}] * 12},
                "output": {"w": 3840, "h": 2160, "hz": self.hz}}

    # game process (watch)
    def running(self, appid):  # the watch interface
        return appid in self.running_set

    @property
    def running_set(self):
        return self.__dict__.setdefault("_run", set())

    def play(self, appid, seconds, log=None):
        """The game starts, runs `seconds` and ends; MangoHud writes `log` (frametimes) if given."""
        self.running_set.add(appid)
        self.now += 5
        assert self.e.tick() == "started"
        self.now += seconds
        if log is not None:
            s = self.e._session()
            if s and s.get("measure"):
                write_log(os.path.join(s["measure"]["folder"], f"Cyberpunk2077_{int(self.now)}.csv"), *log)
        self.running_set.discard(appid)
        return self.e.tick()


class FakePerf:
    def __init__(self, validated=1850):
        self.validated = validated
        self.calls = []
        self.refuse = set()
        self.per_game = {}
        self.safe_mode = False

    def state(self):
        return {"profile": "balanced", "safe_mode": self.safe_mode, "per_game": dict(self.per_game),
                "range": {"validated_max": self.validated}, "profiles": {"silent": {}, "balanced": {}, "performance": {}}}

    def capabilities(self):
        return {"driver": "amdgpu", "governor": True, "cu40": {"active": True}}

    def set_game_profile(self, appid, pid):
        self.calls.append((appid, pid))
        if pid in self.refuse:
            return False, "InvalidValue: not validated"
        if pid == "global":
            self.per_game.pop(appid, None)
        else:
            self.per_game[appid] = pid
        return True, None


def write_log(path, fps=60.0, jitter=0.0, seconds=90, clock=1500, gpu_t=70, cpu_t=72, stutter_every=0):
    rnd = random.Random(7)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["os,cpu,gpu,ram,kernel,driver,cpuscheduler", "Bazzite,AMD BC-250,Cyan Skillfish,16GB,6.17,Mesa,",
             "fps,frametime,cpu_load,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,gpu_power,elapsed"]
    t, n = 0.0, 0
    while t < seconds:
        ft = 1000.0 / fps * (1 + rnd.uniform(-jitter, jitter))
        if stutter_every and n % stutter_every == stutter_every - 1:
            ft *= 4
        t += ft / 1000
        n += 1
        lines.append(f"{1000 / ft:.1f},{ft:.3f},40,99,{cpu_t},{gpu_t},{clock},1750,95,{int(t * 1e9)}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def measured(w, cfg_path, preset, fps, clock=1500, jitter=0.02, perf="balanced", gpu_t=70):
    """One complete measurement launch of the given configuration."""
    c = optimizer._cfg(cfg_path, preset, perf=perf)
    r = w.e.launch(CP, measure={"config": c, "delay": 30, "duration": 90})
    assert r["ok"], r
    return w.play(CP, 200, log=(fps, jitter, 90, clock, gpu_t))


# ---------------------------------------------------------------- tests
@test
def G01_profile_loading_layers_and_database_version():
    with World():
        p = store.load(CP)
        assert p["status"] == "UNKNOWN" and p["source"] == "builtin" and p["db_version"] == 1 and p["errors"] == []
        assert p["game"]["anti_cheat"] is False and "fsr" in p["game"]["native_upscalers"] and len(p["candidates"]) == 8
        assert p["modes"]["recommended"]["graphics"]["path"] == "game_upscaler"
        # user > auto > built-in, per field
        auto = dict(schema.empty_profile(CP, "Cyberpunk 2077"), source="auto", status="AUTO_GENERATED")
        auto["modes"] = {"recommended": optimizer._cfg("native")}
        store.write_layer("auto", auto)
        user = dict(schema.empty_profile(CP), status="USER_MODIFIED")
        user["modes"] = {"custom": optimizer._cfg("game_upscaler", "balanced")}
        store.write_layer("user", user)
        p = store.load(CP)
        assert p["status"] == "USER_MODIFIED" and p["layers"] == ["auto", "builtin", "user"]
        assert p["modes"]["recommended"]["graphics"]["path"] == "native" and "custom" in p["modes"]
        assert p["game"]["native_upscalers"]                 # facts survive from the built-in layer
        # a broken file is ignored and reported, never trusted
        with open(os.path.join(store.data_dir(), "user", f"{CP}.json"), "w") as f:
            f.write("{not json")
        p = store.load(CP)
        assert p["status"] == "AUTO_GENERATED" and p["errors"] and "user" in p["errors"][0]


@test
def G02_profile_validation():
    ok = optimizer._cfg("helixsr", "quality")
    assert ok["graphics"]["internal"] == {"strategy": "upscaled", "scale": 0.667}
    bad = [{"graphics": {"path": "dlss"}}, {"graphics": {"path": "native", "internal": {"strategy": "native", "scale": 0.1}}},
           {"frame_target": {"fps": 1000}}, {"frame_target": {"fps": True}}, {"graphics": {"frame_generation": "yes"}},
           {"performance": {"profile": "overclock"}}, {"gamescope": {"scaling_filter": "magic"}}, "x"]
    for b in bad:
        try:
            schema.clean_config(b)
            raise AssertionError(b)
        except schema.ProfileError:
            pass
    for raw, src in (({"schema": 2, "appid": "1"}, "builtin"), ({"schema": 1, "appid": "x1"}, "builtin"),
                     ({"schema": 1, "appid": "1", "status": "HARDWARE_VERIFIED"}, "builtin"),
                     ({"schema": 1, "appid": "1", "status": "STONE_VERIFIED"}, "auto")):
        try:
            schema.clean_profile(raw, src)
            raise AssertionError(raw)
        except schema.ProfileError:
            pass
    # every built-in profile passes the run-time rules
    for f in os.listdir(os.path.join(BUILTIN, "steam")):
        with open(os.path.join(BUILTIN, "steam", f)) as fh:
            schema.clean_profile(json.load(fh), "builtin")


@test
def G03_detection_from_disk():
    with World(injected="helixsr", proxy="winmm") as w:
        f = detect.facts(w.root, CP)
        assert f["installed"] and f["build"] == "19000000" and f["proton"] == "proton_9" and f["anti_cheat"] == []
        assert f["injected"]["kind"] == "helixsr" and f["injected"]["proxy"] == "winmm" and f["injected"]["fingerprint"]
        fp = f["injected"]["fingerprint"]
        with open(os.path.join(w.game_dir, "bin", "x64", "HelixSR", "helixsr.dll"), "ab") as fh:
            fh.write(b"new version")                          # HelixSR updated -> new fingerprint
        assert detect.facts(w.root, CP)["injected"]["fingerprint"] != fp
        assert detect.facts(w.root, "777")["installed"] is False
    with World(injected="optiscaler", proxy="dxgi", anti_cheat=True) as w:
        f = detect.facts(w.root, CP)
        assert f["injected"]["kind"] == "optiscaler" and f["injected"]["proxy"] == "dxgi" and f["anti_cheat"] == ["EasyAntiCheat"]


@test
def G04_graphics_availability_and_helixsr_unavailable():
    game = store.load(CP)["game"]
    with World(injected="helixsr", proxy="winmm") as w:
        a = graphics.availability(game, detect.facts(w.root, CP))
        assert all(a[k]["available"] for k in ("native", "game_upscaler", "helixsr")) and not a["optiscaler"]["available"]
        assert a["gamescope"]["guidance_only"]
        assert graphics.env_for(optimizer._cfg("helixsr", "quality"), detect.facts(w.root, CP))["WINEDLLOVERRIDES"] == "winmm=n,b"
        assert graphics.env_for(optimizer._cfg("native"), detect.facts(w.root, CP)) == {}
        a = graphics.availability(game, detect.facts(w.root, CP), 'WINEDLLOVERRIDES="winmm=n,b" %command%')
        assert not a["native"]["available"] and "launch options" in a["native"]["reason"]
    with World() as w:                                        # HELIXSR UNAVAILABLE: not installed
        a = graphics.availability(game, detect.facts(w.root, CP))
        assert not a["helixsr"]["available"] and a["helixsr"]["reason"] == "not installed in the game folder"
        p = w.e.profile(CP)
        rec = w.e.recommended(CP, p=p)
        assert rec["config"]["graphics"]["path"] == "game_upscaler"
        assert all(c["graphics"]["path"] != "helixsr" for c in optimizer.matrix(p, p["availability"]))
    with World(injected="helixsr", proxy="dxgi") as w:       # always loaded: no clean native path
        a = graphics.availability(game, detect.facts(w.root, CP))
        assert not a["native"]["available"] and not a["game_upscaler"]["available"] and a["helixsr"]["available"]
        assert "winmm" in a["native"]["reason"]


@test
def G05_anti_cheat_and_online_safe_path():
    game = store.load(CP)["game"]
    with World(injected="helixsr", anti_cheat=True) as w:
        p = w.e.profile(CP)
        assert not p["availability"]["helixsr"]["available"] and "anti-cheat" in p["availability"]["helixsr"]["reason"]
        assert p["availability"]["game_upscaler"]["available"]
        rec = w.e.recommended(CP, p=p)
        assert rec["config"]["graphics"]["path"] == "game_upscaler"
        # even a person's CUSTOM HelixSR configuration is not injected into an anti-cheat game
        w.e.apply(CP, "custom", optimizer._cfg("helixsr", "quality"))
        r = w.e.launch(CP, "custom")
        assert r["ok"] and r["config"]["graphics"]["path"] == "game_upscaler"
        assert "WINEDLLOVERRIDES" not in w.options[CP]
    online = dict(game, online_only=True, anti_cheat=None)
    with World(injected="optiscaler") as w:
        assert not graphics.availability(online, detect.facts(w.root, CP))["optiscaler"]["available"]


@test
def G06_launch_options_kept_restored_and_recovered():
    assert graphics.compose("gamemoderun %command% -skipStartScreen", {"WINEDLLOVERRIDES": "winmm=n,b"}) == \
        "WINEDLLOVERRIDES=winmm=n,b gamemoderun %command% -skipStartScreen"
    assert graphics.compose("-skipStartScreen", {"DXVK_FRAME_RATE": "40"}) == "DXVK_FRAME_RATE=40 %command% -skipStartScreen"
    assert graphics.compose("DXVK_FRAME_RATE=30 %command%", {"DXVK_FRAME_RATE": "40"}) == "DXVK_FRAME_RATE=30 %command%"
    with World(injected="helixsr", user_options="-skipStartScreen") as w:
        r = w.e.launch(CP, measure={"config": optimizer._cfg("helixsr", "quality"), "delay": 30, "duration": 90})
        assert r["ok"] and r["graphics_applied"] and w.launched == [int(CP)]
        assert w.options[CP].startswith("WINEDLLOVERRIDES=winmm=n,b MANGOHUD=1") and w.options[CP].endswith("%command% -skipStartScreen")
        # STONE Home dies with the game running; at the next start the game is gone: recovered
        w.running_set.add(CP)
        assert w.e.tick() == "started"
        w.running_set.discard(CP)
        e2 = optimizer.Engine(root=lambda: w.root, cef=w, display=w.display, perf=w.perf, watch=w, clock=lambda: w.now, log=lambda m: None)
        out = e2.recover()
        assert out["session"]["reason"] == "exit" or out["session"]["abnormal"] in (True, False)
        assert w.options[CP] == "-skipStartScreen" and e2.status() == {"active": False}
        assert w.perf.per_game == {}
        # the person edits the options while the game runs: theirs are kept
        r = w.e.launch(CP, measure={"config": optimizer._cfg("helixsr", "balanced"), "delay": 30, "duration": 90})
        w.options[CP] = "PROTON_LOG=1 %command%"
        w.running_set.add(CP)
        w.e.tick()
        w.running_set.discard(CP)
        w.now += 300
        assert w.e.tick()["graphics_restored"] == "kept (changed by the person)" and w.options[CP] == "PROTON_LOG=1 %command%"
        # Steam's interface not answering: the game launches as the person left it
        w.cef_up = False
        r = w.e.prepare(CP)
        assert r["ok"] and r["graphics_applied"] is False and w.options[CP] == "PROTON_LOG=1 %command%"


@test
def G07_metrics_and_score():
    d = tempfile.mkdtemp()
    try:
        write_log(os.path.join(d, "steady.csv"), fps=60, jitter=0.02)
        write_log(os.path.join(d, "stutter.csv"), fps=60, jitter=0.25, stutter_every=50)
        write_log(os.path.join(d, "short.csv"), fps=60, seconds=10)
        with open(os.path.join(d, "broken.csv"), "w") as f:
            f.write("garbage\n1,2,3\n")
        a, b = metrics.summarize(os.path.join(d, "steady.csv")), metrics.summarize(os.path.join(d, "stutter.csv"))
        assert 58 < a["avg_fps"] < 62 and a["low1_fps"] > 55 and a["frametime_cv"] < 0.03 and a["stutters"] == 0
        assert a["gpu_clock_avg"] == 1500 and a["gpu_temp_max"] == 70 and a["power_reliable"] is False
        assert b["low1_fps"] < 30 and b["stutters"] > 50 and b["frametime_cv"] > a["frametime_cv"]
        assert "too little" in metrics.summarize(os.path.join(d, "short.csv"))["error"]
        assert "frametime" in metrics.summarize(os.path.join(d, "broken.csv"))["error"]
        cfg = optimizer._cfg("game_upscaler", "quality")
        sa, sb = score.score(a, cfg, "recommended", 60), score.score(b, cfg, "recommended", 60)
        assert sa["score"] > 80 and sb["score"] < sa["score"] - 15          # not reduced to average FPS
        assert score.score(a, cfg, "recommended", 60, crashed=True)["score"] == 0
        assert score.score({"error": "x"}, cfg, "recommended", 60)["score"] is None
        hot = dict(a, gpu_temp_max=91)
        assert score.score(hot, cfg, "recommended", 60)["gate"] == "too hot"
        slow = dict(a, avg_fps=25, low1_fps=20)
        assert score.score(slow, cfg, "recommended", 60)["score"] <= 40
        # QUALITY values the image more than PERFORMANCE does
        nat = optimizer._cfg("native")
        assert score.quality_prior(nat) > score.quality_prior(cfg) > score.quality_prior(optimizer._cfg("game_upscaler", "performance"))
        assert score.WEIGHTS["quality"]["Q"] > score.WEIGHTS["recommended"]["Q"] > score.WEIGHTS["performance"]["Q"]
        assert all(sum(w.values()) == 100 for w in score.WEIGHTS.values())
        assert metrics.mangohud_env("/x", 60, 120)["MANGOHUD_CONFIG"] == "no_display,log_interval=0,output_folder=/x,autostart_log=60,log_duration=120"
    finally:
        shutil.rmtree(d)


@test
def G08_display_changes_and_unknown_display():
    t60 = score.targets(optimizer.display_input({"primary": {"native_resolution": {"w": 3840, "h": 2160}}, "output": {"w": 3840, "h": 2160, "hz": 60}}))
    assert t60["targets"] == {"performance": 60, "recommended": 60, "quality": 30, "custom": 60}
    with World(hz=120, vrr=True) as w:
        p = w.e.profile(CP)
        assert p["targets"]["targets"]["quality"] == 48 and p["targets"]["vrr"]       # no rigid 60
        assert p["targets"]["targets"]["performance"] == 90
        measured(w, "game_upscaler", "quality", 75)
        assert w.e.profile(CP)["stale"] == []
        rec = w.e.recommended(CP)
        assert rec["why"].startswith("measured") and rec["config"]["frame_target"]["fps"] is not None
        w.hz, w.vrr = 60, False                             # another TV: measurements no longer apply
        p = w.e.profile(CP)
        assert p["stale"] == ["display"] and w.e.recommended(CP, p=p)["why"].startswith("STONE profile database")
    with World(display=False) as w:                          # UNKNOWN DISPLAY
        p = w.e.profile(CP)
        assert p["display"]["known"] is False and p["targets"]["targets"]["recommended"] == 60
        assert p["environment"]["display"] == "unknown"
        r = w.e.launch(CP)
        assert r["ok"] and w.launched == [int(CP)] and w.e.recommended(CP)["internal"] is None


@test
def G09_game_exit_saves_metrics_restores_performance_cleans_graphics():
    with World(injected="helixsr") as w:
        nxt = w.e.profile(CP)["next_measurement"]
        assert nxt["graphics"]["path"] == "native"           # matrix order: built-in reference first
        res = measured(w, "helixsr", "quality", 62, clock=1600)
        assert res["reason"] == "exit" and not res["abnormal"] and res["metrics"]["avg_fps"] > 60
        assert res["performance_restored"] is True and res["graphics_restored"] == "restored"
        assert w.options[CP] == "" and w.perf.per_game == {} and (CP, "balanced") in w.perf.calls
        auto, _ = store.read_layer("auto", CP)
        assert auto["status"] == "AUTO_GENERATED" and len(auto["measurements"]) == 1
        m = auto["measurements"][0]
        assert m["internal"] == {"w": 2561, "h": 1441, "expected": True} and m["output"] == {"w": 3840, "h": 2160}
        assert m["score"]["recommended"]["score"] is not None
        p = store.load(CP)
        assert p["status"] == "AUTO_GENERATED" and p["modes"]["recommended"]["graphics"]["path"] == "helixsr"
        # LOAD PROFILE -> VERIFY -> PLAY: a normal launch uses it, no new benchmark
        r = w.e.launch(CP)
        assert r["ok"] and r["measure"] is None and "WINEDLLOVERRIDES=winmm=n,b" in w.options[CP] and "MANGOHUD" not in w.options[CP]
        assert r["config"]["frame_target"]["fps"] == 60 and "DXVK_FRAME_RATE=60" in w.options[CP]
        res = w.play(CP, 1800)
        assert not res["abnormal"] and "metrics" not in res and w.options[CP] == ""


@test
def G10_game_crash_graphics_rollback_and_suspect():
    with World(injected="helixsr") as w:
        measured(w, "helixsr", "quality", 62)
        measured(w, "game_upscaler", "quality", 55)
        assert w.e.recommended(CP)["config"]["graphics"]["path"] == "helixsr"
        w.e.launch(CP)
        res = w.play(CP, 20)                                 # GAME CRASH: gone after 20 s
        assert res["abnormal"] and res["collected"]["config"]["graphics"]["path"] == "helixsr"
        assert w.options[CP] == "" and w.perf.per_game == {}
        rec = w.e.recommended(CP)                            # SAFE NEXT LAUNCH: HelixSR -> game FSR, clean
        assert rec["config"]["graphics"]["path"] == "game_upscaler" and rec["trail"]
        r = w.e.launch(CP)
        assert "WINEDLLOVERRIDES" not in w.options[CP]
        w.play(CP, 900)                                      # the safe launch ends normally
        assert w.e.recommended(CP)["config"]["graphics"]["path"] == "helixsr"    # back to the normal choice
        w.e.launch(CP)
        w.play(CP, 10)                                       # second crash: SUSPECT
        p = w.e.profile(CP)
        assert optimizer.suspect(p, optimizer._cfg("helixsr", "quality"))
        w.e.launch(CP)
        w.play(CP, 900)
        assert w.e.recommended(CP)["config"]["graphics"]["path"] == "game_upscaler"     # stays out
        assert all(not (c["graphics"]["path"] == "helixsr" and c["graphics"]["upscaler_preset"] == "quality")
                   for c in [optimizer.next_candidate(p, p["availability"], p["environment"])] if c)
        assert r["ok"]


@test
def G11_performance_rollback_independent_of_graphics():
    with World() as w:
        measured(w, "game_upscaler", "quality", 50, clock=1500)
        measured(w, "game_upscaler", "quality", 60, clock=1840, perf="performance")
        rec = w.e.recommended(CP)
        assert rec["config"]["performance"]["profile"] == "performance"
        r = w.e.launch(CP)
        assert w.perf.per_game == {CP: "performance"} and r["performance"] == "performance"
        res = w.play(CP, 15)                                 # crash on PERFORMANCE
        assert res["abnormal"] and w.perf.per_game == {}
        rec = w.e.recommended(CP)
        assert rec["config"]["performance"]["profile"] == "balanced"            # performance falls back...
        assert rec["config"]["graphics"]["path"] == "game_upscaler"             # ...graphics untouched (no injection to blame)
        # stoned refuses PERFORMANCE (not validated after a new calibration): BALANCED
        w.e.launch(CP)
        w.play(CP, 900)
        w.perf.refuse.add("performance")
        r = w.e.launch(CP)
        assert r["performance"] == "balanced"
        w.play(CP, 900)
        # a per-game profile the person chose in STONE Performance is kept and never cleared
        w.perf.refuse.clear()
        w.perf.per_game[CP] = "silent"
        r = w.e.launch(CP)
        assert r["performance"] == "silent"
        w.play(CP, 900)
        assert w.perf.per_game == {CP: "silent"}
        # stoned in Safe Mode: STONE does not ask for any profile
        w.perf.per_game.clear()
        w.perf.safe_mode = True
        n = len(w.perf.calls)
        w.e.launch(CP)
        w.play(CP, 900)
        assert len(w.perf.calls) == n


@test
def G12_performance_capability_change_invalidates():
    with World() as w:
        measured(w, "game_upscaler", "quality", 60)
        assert w.e.recommended(CP)["why"].startswith("measured")
        w.perf.validated = 2000                              # new calibration
        p = w.e.profile(CP)
        assert "calibration" in p["stale"] and not w.e.recommended(CP, p=p)["why"].startswith("measured")
        assert p["next_measurement"] is not None
        w.perf.validated = 1850
        w.set_proton("proton_10")                            # Proton changed
        assert "proton" in w.e.profile(CP)["stale"]
        w.set_proton("proton_9")
        w.write_manifest(CP, "Cyberpunk 2077", "Cyberpunk 2077", build="19500000")   # game updated
        assert "game_build" in w.e.profile(CP)["stale"]
        # a measurement under the new environment replaces the old ones
        measured(w, "game_upscaler", "balanced", 66)
        auto, _ = store.read_layer("auto", CP)
        assert len(auto["measurements"]) == 1 and auto["invalidation"]["game_build"] == "19500000"


@test
def G13_unknown_game_changes_nothing():
    with World() as w:
        p = w.e.profile("4242")
        assert p["status"] == "UNKNOWN" and p["layers"] == [] and p["game"]["native_upscalers"] == []
        rec = w.e.recommended("4242", p=p)
        assert rec["config"]["graphics"]["path"] == "native" and rec["why"].startswith("safe")
        r = w.e.launch("4242")
        assert r["ok"] and w.options["4242"] == "" and w.set_calls == [] and w.perf.calls == []
        assert w.launched == [4242]
        res = w.play("4242", 600)
        assert res["graphics_restored"] == "nothing"
        nothing = w.e.profile("777")                          # not even installed
        assert nothing["facts"]["installed"] is False and w.e.recommended("777", p=nothing)["config"]["graphics"]["path"] == "native"


@test
def G14_efficiency_and_helixsr_preference_never_forced():
    with World(injected="helixsr") as w:
        measured(w, "game_upscaler", "quality", 60, clock=1840, perf="performance")
        measured(w, "game_upscaler", "quality", 60, clock=1600)
        rec = w.e.recommended(CP)["config"]
        assert rec["performance"]["profile"] == "balanced"   # same experience, lower clock: EFFICIENCY
        measured(w, "helixsr", "quality", 60, clock=1600)
        assert w.e.recommended(CP)["config"]["graphics"]["path"] == "helixsr"   # equal: HelixSR preferred
    with World(injected="helixsr") as w:
        measured(w, "game_upscaler", "quality", 60, clock=1600)
        measured(w, "helixsr", "quality", 45, clock=1600, )
        assert w.e.recommended(CP)["config"]["graphics"]["path"] == "game_upscaler"   # worse: never forced
        assert w.e.recommended(CP, "quality")["config"]["graphics"]["path"] in ("game_upscaler", "helixsr")


@test
def G15_hardware_verified_only_from_the_person():
    with World() as w:
        try:
            w.e.verify(CP)                                  # nothing measured: refused
            raise AssertionError("verified without data")
        except schema.ProfileError:
            pass
        measured(w, "game_upscaler", "quality", 60)
        assert w.e.verify(CP)["status"] == "HARDWARE_VERIFIED" and store.load(CP)["status"] == "HARDWARE_VERIFIED"
        measured(w, "game_upscaler", "balanced", 64)           # new data: the verification was of the old
        assert store.load(CP)["status"] == "AUTO_GENERATED"
        ex = json.dumps(store.read_layer("auto", CP)[0])
        assert "STONE_VERIFIED" not in ex


@test
def G16_modes_custom_and_restore_recommended():
    with World(injected="helixsr") as w:
        modes = {m["mode"]: m for m in w.e.modes(CP)}
        assert modes["recommended"]["default"] and modes["custom"]["available"] is False
        w.e.apply(CP, "custom", {"graphics": {"path": "helixsr", "upscaler_preset": "balanced",
                                              "internal": {"strategy": "upscaled", "scale": 0.588}},
                                 "performance": {"profile": "silent"}, "frame_target": {"fps": 40}})
        w.e.apply(CP, "custom")
        assert w.e.chosen_mode(CP) == "custom" and store.load(CP)["status"] == "USER_MODIFIED"
        r = w.e.launch(CP)
        assert r["config"]["graphics"]["path"] == "helixsr" and "DXVK_FRAME_RATE=40" in w.options[CP] and r["performance"] == "silent"
        w.play(CP, 900)
        assert w.e.restore_recommended(CP)["removed_user_profile"] and w.e.chosen_mode(CP) == "recommended"
        assert store.load(CP)["status"] == "UNKNOWN"
        assert w.e.status() == {"active": False}


@test
def G17_stage_two_tries_performance_ranges_of_the_best():
    with World() as w:
        p = w.e.profile(CP)
        rows = optimizer.matrix(p, p["availability"])
        assert [c["graphics"]["path"] for c in rows] == ["native", "game_upscaler", "game_upscaler", "game_upscaler"]
        assert all(not c["graphics"]["frame_generation"] and c["performance"]["profile"] == "balanced" for c in rows)
        for c, fps in zip(rows, (24, 50, 58, 64), strict=True):
            r = w.e.launch(CP, measure={"config": c, "delay": 30, "duration": 90})
            assert r["ok"]
            w.play(CP, 200, log=(fps, 0.02, 90, 1500, 70))
        p = w.e.profile(CP)
        nxt = p["next_measurement"]
        assert nxt is not None and nxt["performance"]["profile"] in ("silent", "performance")
        s2 = optimizer.stage2(p, p["availability"], p["environment"])
        assert len(s2) == 4 and {c["performance"]["profile"] for c in s2} == {"silent", "performance"}


@test
def G18_host_api_game_to_recommended_to_play():
    from gi.repository import GLib

    from stone_home import host as host_mod
    from stone_home import system

    class Stoned:
        def __init__(self, perf):
            self.perf = perf

        def call(self, method, args, done):
            if method == "getState":
                return done(self.perf.state())
            if method == "getCapabilities":
                return done(self.perf.capabilities())
            if method == "setGameProfile":
                ok, err = self.perf.set_game_profile(*args)
                return done({"ok": True}) if ok else done(None, "InvalidValue", err)
            done(None, "InvalidValue", "unknown")

    def run(h, method, *args):
        box, loop = {}, GLib.MainLoop()

        def done(result=None, code=None, message=""):
            box["r"] = (result, code)
            loop.quit()
        GLib.idle_add(lambda: (h.handle({"method": method, "args": list(args)}, done), False)[1])
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        return box.get("r")

    with World(injected="helixsr") as w:
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(w.d, "config")
        try:
            h = host_mod.Host(lambda *a: None, stoned=Stoned(w.perf), sysapi=system.System(spawn=lambda u: None), steam_root=w.root)
            h.steam_ui = w
            h.display.status = lambda fresh=False: w.display()
            h.optimized.clock = lambda: w.now
            h.optimized.watch = w
            r, code = run(h, "game.recommended", CP)
            assert code is None and r["label"] == "STONE RECOMMENDED" and r["status"] == "UNKNOWN", r
            assert r["summary"].startswith("FSR del juego Calidad") and r["output"] == {"w": 3840, "h": 2160}
            assert run(h, "game.recommended", CP, "turbo")[1] == "InvalidValue"
            assert run(h, "game.recommended", "1; rm")[1] == "InvalidValue"
            r, code = run(h, "game.profile", CP)
            assert code is None and r["availability"]["helixsr"]["available"] and r["injected"] == "helixsr"
            r, code = run(h, "game.modes", CP)
            assert code is None and r["chosen"] == "recommended" and [m["mode"] for m in r["modes"]][0] == "recommended"
            assert run(h, "game.apply", CP, "custom")[1] == "InvalidValue"          # custom needs a configuration
            assert run(h, "game.status")[0] == {"active": False}
            # JUGAR: the library scan, then launch -> prepare (graphics + performance) -> Steam
            run(h, "library")
            r, code = run(h, "launch", CP)
            assert code is None and r["via"] == "steam-ui" and w.launched == [int(CP)]
            st, _ = run(h, "game.status")
            assert st["active"] and st["appid"] == CP and st["graphics_applied"]
            assert w.options[CP] == ""                    # built-in starting point: nothing injected
            w.play(CP, 600)
            assert run(h, "game.status")[0] == {"active": False}
            # prefs: STONE OPTIMIZED can be turned off; JUGAR then launches untouched
            system.save_prefs(dict(system.load_prefs(), optimized=0))
            run(h, "launch", CP)
            assert not os.path.exists(h.optimized.session_path)
            assert run(h, "game.restoreRecommended", CP)[0]["ok"]
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old


@test
def G19_image_choice_is_explicit_available_only_and_reversible():
    with World(injected="helixsr") as w:
        o = w.e.image_options(CP)
        keys = {c["key"]: c for c in o["choices"]}
        assert o["mode"] == "recommended" and keys["auto"]["selected"] and keys["helixsr"]["available"], o
        assert "optiscaler" not in keys                         # not installed: not listed
        r = w.e.set_image(CP, "helixsr", "balanced")
        assert r["mode"] == "custom"
        g = w.e.recommended(CP, "custom")["config"]["graphics"]
        assert g["path"] == "helixsr" and g["upscaler_preset"] == "balanced" and g["internal"]["strategy"] == "upscaled", g
        assert {c["key"]: c for c in w.e.image_options(CP)["choices"]}["helixsr"]["selected"]
        w.e.set_image(CP, "native")
        g = w.e.recommended(CP, "custom")["config"]["graphics"]
        assert g["path"] == "native" and g["upscaler_preset"] is None and g["internal"]["scale"] == 1.0, g
        w.e.set_image(CP, "gamescope", "performance")
        assert w.e.recommended(CP, "custom")["config"]["gamescope"]["scaling_filter"] == "fsr"
        for bad in (("banana", None), ("game_upscaler", "turbo")):
            try:
                w.e.set_image(CP, *bad)
                raise AssertionError(bad)
            except schema.ProfileError:
                pass
        assert w.e.set_image(CP, "auto")["mode"] == "recommended" and w.e.chosen_mode(CP) == "recommended"
    with World(injected=None) as w:                              # HelixSR not installed: refused, not faked
        keys = {c["key"] for c in w.e.image_options(CP)["choices"]}
        assert "helixsr" not in keys, keys
        try:
            w.e.set_image(CP, "helixsr")
            raise AssertionError("helixsr without install")
        except schema.ProfileError as e:
            assert "not installed" in str(e), e


def _release_zip(files):
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return b.getvalue()


def _fake_net(kind, files, tag="v9.9.9", name=None, digest=True, bad_digest=False):
    import hashlib
    blob = _release_zip(files)
    name = name or ("HelixSR-9.9.9.zip" if kind == "helixsr" else "OptiScaler_9.9.9.zip")
    sha = hashlib.sha256(blob).hexdigest()

    def get_json(url):
        assert url.startswith("https://api.github.com/repos/"), url
        a = {"name": name, "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/{name}", "size": len(blob)}
        if digest:
            a["digest"] = "sha256:" + ("0" * 64 if bad_digest else sha)
        return {"tag_name": tag, "prerelease": False, "assets": [{"name": "notes.txt"}, a]}

    def download(url, dest):
        with open(dest, "wb") as f:
            f.write(blob)
        return sha, len(blob)
    return get_json, download


@test
def G20_components_download_verify_install_uninstall():
    from stone_games import install
    opti = {"OptiScaler/OptiScaler.dll": b"MZ" + bytes(50), "OptiScaler/OptiScaler.ini": "[Upscalers]\n",
            "OptiScaler/D3D12_Optiscaler/D3D12Core.dll": b"MZ"}
    with World() as w:
        gdir = os.path.join(w.game_dir, "bin", "x64")
        with open(os.path.join(gdir, "Cyberpunk2077.exe"), "wb") as f:
            f.write(bytes(5000))
        with open(os.path.join(w.game_dir, "REDlauncher.exe"), "wb") as f:
            f.write(bytes(9000))                                    # a launcher is not the game
        # nothing downloaded yet: the plan says so and changes nothing
        r = w.e.install_plan(CP, "optiscaler")
        assert not r["ok"] and r["needs_download"], r
        # a wrong checksum is refused and nothing is kept
        gj, dl = _fake_net("optiscaler", opti, bad_digest=True)
        try:
            install.fetch("optiscaler", gj, dl)
            raise AssertionError("bad digest accepted")
        except install.InstallError as e:
            assert "SHA-256" in str(e)
        assert install.have("optiscaler") is None
        # only GitHub / its CDN hosts are ever contacted
        try:
            install._check_url("https://evil.example/x.zip")
            raise AssertionError("host allowed")
        except install.InstallError:
            pass
        gj, dl = _fake_net("optiscaler", opti)
        rec = install.fetch("optiscaler", gj, dl)
        assert rec["tag"] == "v9.9.9" and rec["verified_by_github"] and install.have("optiscaler")["tag"] == "v9.9.9"
        assert install.fetch("optiscaler", gj, dl).get("already")       # a second press downloads nothing
        # the plan names the real executable folder; nothing is written yet
        plan = w.e.install_plan(CP, "optiscaler")
        assert plan["ok"] and plan["exe"] == "Cyberpunk2077.exe" and plan["folder"].replace(os.sep, "/") == "bin/x64" and plan["files"] == 3, plan
        assert not os.path.exists(os.path.join(gdir, "winmm.dll"))
        r = w.e.install_image(CP)
        assert r["ok"], r
        assert os.path.isfile(os.path.join(gdir, "winmm.dll")) and os.path.isfile(os.path.join(gdir, "OptiScaler.ini"))
        assert os.path.isfile(os.path.join(gdir, "D3D12_Optiscaler", "D3D12Core.dll"))
        assert not os.path.exists(os.path.join(gdir, "OptiScaler.dll"))           # renamed to the proxy
        # STONE sees it like any installed OptiScaler and can switch it per launch
        keys = {c["key"]: c for c in w.e.image_options(CP)["choices"]}
        assert keys["optiscaler"]["available"], keys["optiscaler"]
        assert not w.e.install_plan(CP, "optiscaler")["ok"]                         # already installed
        u = w.e.uninstall_image(CP)
        assert u["ok"] and u["removed"] == 3
        assert not os.path.exists(os.path.join(gdir, "winmm.dll")) and not os.path.exists(os.path.join(gdir, "D3D12_Optiscaler"))
        assert os.path.isfile(os.path.join(gdir, "Cyberpunk2077.exe"))              # the game's own files untouched
        assert not w.e.uninstall_image(CP)["ok"]
        # never over existing files
        os.makedirs(os.path.join(gdir, "D3D12_Optiscaler"))
        with open(os.path.join(gdir, "D3D12_Optiscaler", "D3D12Core.dll"), "w") as f:
            f.write("mine")
        assert "already exist" in w.e.install_plan(CP, "optiscaler")["error"]
    with World(anti_cheat=True) as w:                                               # anti-cheat: refused
        gj, dl = _fake_net("optiscaler", opti)
        install.fetch("optiscaler", gj, dl)
        with open(os.path.join(w.game_dir, "bin", "x64", "g.exe"), "wb") as f:
            f.write(bytes(100))
        r = w.e.install_plan(CP, "optiscaler")
        assert not r["ok"] and "anti-cheat" in r["error"], r
        assert not w.e.install_image(CP)["ok"]


@test
def G21_helixsr_is_downloaded_and_the_person_runs_the_authors_installer():
    from stone_games import install
    with World() as w:
        assert not w.e.install_plan(CP, "helixsr")["ok"]
        gj, dl = _fake_net("helixsr", {"HelixSR-9.9.9/helixsr-setup.sh": "#!/bin/sh\n", "HelixSR-9.9.9/README.md": "x"})
        install.fetch("helixsr", gj, dl)
        r = w.e.install_plan(CP, "helixsr")
        assert r["ok"] and "./helixsr-setup.sh" in r["command"] and "--yes" in r["warning"], r
        assert "--yes" not in r["command"]
        sh = os.path.join(r["dir"], "helixsr-setup.sh")
        assert os.access(sh, os.X_OK)
        try:
            w.e.install_image(CP, "helixsr")                         # STONE never runs it for the person
            raise AssertionError("helixsr installed by STONE")
        except schema.ProfileError:
            pass
        # an archive that tries to write outside its folder is refused
        gj, dl = _fake_net("helixsr", {"../evil.sh": "x"}, tag="v9.9.10")
        try:
            install.fetch("helixsr", gj, dl)
            raise AssertionError("path traversal accepted")
        except (install.InstallError, ValueError, OSError):
            pass


@test
def G22_guided_optimization_counts_the_matrix_and_grows_with_installed_paths():
    with World() as w:
        o = w.e.optimize_status(CP)
        assert o["done"] == 0 and o["total"] >= 4 and o["next"] and o["status"] == "UNKNOWN", o
        r = w.e.launch(CP, measure={"config": "next", "delay": 30, "duration": 90})
        assert r["ok"]
        w.play(CP, 200, log=(50, 0.02, 90, 1500, 70))
        o2 = w.e.optimize_status(CP)
        assert o2["done"] == 1 and o2["measured"] == 1 and o2["total"] >= o["total"], o2
        assert o2["next"] != o["next"], (o, o2)
    with World(injected="helixsr") as w2:                       # HelixSR installed -> more rows to measure
        assert w2.e.optimize_status(CP)["total"] > o["total"]


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
    print("\nall passed" if not failed else f"\n{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
