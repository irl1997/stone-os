"""STONE DISPLAY: EDID parsing, kernel connectors, Steam's display state, output policy and the
game environment, against synthetic displays (mocks are only in this file, never in production).

Display matrix: 1080p60, 1080p120, 1440p60, 1440p120, 4K30, 4K60, 4K120, 4K VRR, 4K no VRR,
HDR, no HDR, unknown EDID, disconnected, display changed, multiple outputs.
Game matrix: known game, unknown game (no STONE profile), game with a size the person chose.

    python3 tests/test_display.py
"""

import base64
import json
import os
import re
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "system_files", "usr", "lib", "stone"))

import stone_display as SD  # noqa: E402
from stone_display import cli, drm, edid, model, steam  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# ---------------------------------------------------------------- synthetic EDIDs
def _dtd(w, h, hz):
    hb, vb = (280, 45) if h <= 1440 else (560, 90)
    clock = round((w + hb) * (h + vb) * hz / 10_000)
    assert clock < 65536, "pixel clock does not fit a DTD"
    return bytes([clock & 0xFF, clock >> 8, w & 0xFF, hb & 0xFF, ((w >> 8) << 4) | (hb >> 8),
                  h & 0xFF, vb & 0xFF, ((h >> 8) << 4) | (vb >> 8), 88, 44, 0x45, 0, 0, 0, 0, 0, 0, 0x1E])


def _desc(tag, payload):
    return bytes([0, 0, 0, tag, 0]) + payload


def _cs(block):
    block = bytearray(block)
    block[127] = (-sum(block[:127])) % 256
    return bytes(block)


def make_edid(name="STONE TV", native=(3840, 2160, 60), vics=(), hdr=False, hf_vrr=None, freesync=None,
              range_hz=None, continuous=False, hdmi=True, tmds=600, extra_dtds=()):
    b = bytearray(128)
    b[0:8] = edid.HEADER
    v = (ord("S") - 64) << 10 | (ord("T") - 64) << 5 | (ord("N") - 64)
    b[8], b[9] = v >> 8, v & 0xFF
    b[10], b[11] = 0x34, 0x12
    b[12:16] = b"\x01\x02\x03\x04"               # serial: must never be reported
    b[17], b[18], b[19] = 33, 1, 4
    b[24] = 0x01 if continuous else 0
    descs = [_dtd(*native), _desc(0xFC, (name.encode()[:13] + b"\n").ljust(13, b" "))]
    if range_hz:
        descs.append(_desc(0xFD, bytes([range_hz[0], range_hz[1], 30, 160, 60, 0]) + b"\n" + b" " * 6))
    while len(descs) < 4:
        descs.append(_desc(0x10, bytes(13)))
    for k, d in enumerate(descs[:4]):
        b[54 + 18 * k:72 + 18 * k] = d
    b[126] = 1
    ext = bytearray(128)
    ext[0], ext[1] = 0x02, 0x03
    blocks = bytearray()
    if vics:
        blocks += bytes([(2 << 5) | len(vics)]) + bytes(vics)
    if hdmi:
        vsdb = bytes([0x03, 0x0C, 0x00, 0x10, 0x00, 0x00, min(tmds, 340) // 5])
        blocks += bytes([(3 << 5) | len(vsdb)]) + vsdb
        hf = bytearray([0xD8, 0x5D, 0xC4, 0x01, tmds // 5, 0x80, 0x00, 0x00, 0x00, 0x00])
        if hf_vrr:
            hf[8] = (hf_vrr[0] & 0x3F) | ((hf_vrr[1] >> 8) << 6)
            hf[9] = hf_vrr[1] & 0xFF
        blocks += bytes([(3 << 5) | len(hf)]) + hf
    if freesync:
        fs = bytes([0x1A, 0x00, 0x00, 0x01, 0x00, freesync[0], freesync[1]])
        blocks += bytes([(3 << 5) | len(fs)]) + fs
    if hdr:
        h = bytes([0x06, 0b0101, 0x01, 0x78, 0x50])       # SDR + PQ, max luminance code 0x78
        blocks += bytes([(7 << 5) | len(h)]) + h
    ext[4:4 + len(blocks)] = blocks
    ext[2] = 4 + len(blocks)
    off = ext[2]
    for d in extra_dtds:
        ext[off:off + 18] = _dtd(*d)
        off += 18
    return _cs(b) + _cs(ext)


# ---------------------------------------------------------------- fake kernel and Steam
class Box:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="stone-display-")
        os.environ["STONE_SYSFS_ROOT"] = self.tmp
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
        os.makedirs(os.path.join(self.tmp, "sys/class/drm"))

    def connector(self, name, card="card1", status="connected", modes=(), edid_bytes=None):
        d = os.path.join(self.tmp, "sys/class/drm", f"{card}-{name}")
        os.makedirs(d, exist_ok=True)
        for f, v in (("status", status), ("enabled", "enabled" if status == "connected" else "disabled"),
                     ("modes", "\n".join(f"{w}x{h}" for w, h in modes))):
            with open(os.path.join(d, f), "w") as fh:
                fh.write(v + "\n")
        with open(os.path.join(d, "edid"), "wb") as fh:
            fh.write(edid_bytes or b"")

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _vint(n):
    out = bytearray()
    n &= 0xFFFFFFFFFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _f(num, v):
    if isinstance(v, bytes):
        return _vint(num << 3 | 2) + _vint(len(v)) + v
    return _vint(num << 3) + _vint(v)


def steam_state_bytes(modes, current, default=None, native=None, name="HDMI-A-1", vrr=False, hdr=False):
    """CMsgSystemDisplayManagerState as Steam would serialize it."""
    disp = _f(1, 1) + _f(2, name.encode()) + _f(4, 1) + _f(5, 1) + _f(10, current)
    for i, (w, h, hz) in enumerate(modes):
        disp += _f(11, _f(1, i) + _f(2, w) + _f(3, h) + _f(4, hz))
    disp += _f(14, 1 if vrr else 0) + _f(16, 1 if hdr else 0)
    out = _f(1, disp) + _f(2, 1)
    if native:
        out += _f(4, _f(1, native[0]) + _f(2, native[1]))
    if default:
        out += _f(5, _f(1, default[0]) + _f(2, default[1]))
    return out


class FakeSteam:
    """Steam's interface: display state, SetGamescopeInternalResolution, per-app resolution."""

    def __init__(self, state=None, overrides=None, default_takes=True, answer=True):
        self.state = state                       # dict for steam_state_bytes
        self.over = dict(overrides or {})        # appid -> "Default" | "Native" | "1280x720"
        self.default_takes = default_takes
        self.answer = answer
        self.calls = []

    def ui_value(self, expr):
        if not self.answer:
            return None
        if "DisplayManager.GetState" in expr or "S.GetState" in expr:
            if not self.state:
                return json.dumps({"error": "no DisplayManager"})
            return json.dumps({"b64": base64.b64encode(steam_state_bytes(**self.state)).decode()})
        m = re.search(r"SetGamescopeInternalResolution\((\d+),(\d+)\)", expr)
        if m:
            self.calls.append(("default", int(m.group(1)), int(m.group(2))))
            if self.default_takes:
                self.state["default"] = (int(m.group(1)), int(m.group(2)))
            return json.dumps({"ok": True})
        ids = re.search(r"for\(const id of (\[[^\]]*\])", expr)
        if ids:
            ids = json.loads(ids.group(1))
            ch = []
            if "'Native');ch.push" in expr:
                for i in ids:
                    if self.over.get(i, "Default") == "Default":
                        self.over[i] = "Native"
                        ch.append(i)
            else:
                for i in ids:
                    if self.over.get(i) == "Native":
                        self.over[i] = "Default"
                        ch.append(i)
            self.calls.append(("apps", tuple(ids)))
            return json.dumps({"changed": ch})
        return None


def snap(box, steam_fake=None, xw=None, policy="native"):
    os.environ["STONE_XWAYLAND_FAKE"] = json.dumps(xw or {})
    return SD.collect(policy, use_steam=steam_fake is not None, cef=steam_fake)


# ---------------------------------------------------------------- tests
@test
def D01_edid_4k60_hdmi_identity_without_serial():
    e = edid.parse(make_edid(vics=(16, 97), hdr=True))
    assert e["vendor"] == "STN" and e["name"] == "STONE TV" and e["checksum_ok"], e
    assert e["preferred"]["w"] == 3840 and e["preferred"]["h"] == 2160 and round(e["preferred"]["hz"]) == 60, e
    assert {(v["w"], v["h"], v["hz"]) for v in e["vics"]} == {(1920, 1080, 60), (3840, 2160, 60)}, e["vics"]
    assert e["is_hdmi"] and e["hdmi_max_tmds_mhz"] == 600, e
    assert "serial" not in json.dumps(e).lower() and "16909060" not in json.dumps(e)
    assert e["hdr"]["eotfs"] == ["SDR", "HDR10 (PQ)"], e["hdr"]


@test
def D02_edid_vrr_sources():
    assert edid.parse(make_edid(hf_vrr=(40, 120)))["vrr"] == {"min": 40, "max": 120, "source": "HDMI 2.1 VRR"}
    assert edid.parse(make_edid(freesync=(48, 120)))["vrr"]["source"] == "FreeSync"
    e = edid.parse(make_edid(range_hz=(48, 144), continuous=True, hdmi=False))
    assert e["vrr"] == {"min": 48, "max": 144, "source": "EDID range (continuous)"}, e["vrr"]
    e = edid.parse(make_edid(range_hz=(24, 75)))
    assert e["vrr"] is None and e["range_hz"] == (24, 75), e


@test
def D03_edid_garbage_and_truncated():
    assert edid.parse(b"") is None and edid.parse(b"\x00" * 128) is None
    good = make_edid()
    assert edid.parse(good[:130])["preferred"]["w"] == 3840          # extension cut: base block still read
    bad = bytearray(good)
    bad[127] ^= 0xFF
    assert edid.parse(bytes(bad))["checksum_ok"] is False


def _caps(native, vics, steam_modes=None, current=None, **kw):
    box = Box()
    try:
        box.connector("HDMI-A-1", modes=[(native[0], native[1]), (1920, 1080), (1280, 720)],
                      edid_bytes=make_edid(native=native, vics=vics, **kw))
        st = FakeSteam({"modes": steam_modes, "current": current}) if steam_modes else None
        return snap(box, st)
    finally:
        box.close()


@test
def D04_display_matrix_output_policy():
    # (native, vics, steam modes, current id, expected preferred, expected rates at native)
    cases = [
        ("1080p60", (1920, 1080, 60), (16,), None, None, (1920, 1080, 60), [60]),
        ("1080p120", (1920, 1080, 60), (16, 63), None, None, (1920, 1080, 120), [60, 120]),
        ("1440p60", (2560, 1440, 60), (), None, None, (2560, 1440, 60), [60]),
        ("1440p120", (2560, 1440, 60), (), [(2560, 1440, 120), (2560, 1440, 60), (1920, 1080, 60)], 0,
         (2560, 1440, 120), [60, 120]),
        ("4K30 only", (3840, 2160, 30), (95, 16), None, None, (3840, 2160, 30), [30]),
        ("4K60", (3840, 2160, 60), (95, 97, 16), None, None, (3840, 2160, 60), [30, 60]),
        ("4K120 (Steam proves it)", (3840, 2160, 60), (97, 118, 16),
         [(3840, 2160, 120), (3840, 2160, 60), (3840, 2160, 30), (1920, 1080, 120)], 1, (3840, 2160, 120), [30, 60, 120]),
    ]
    for label, native, vics, smodes, cur, want, rates in cases:
        s = _caps(native, vics, smodes, cur)
        p, pref = s["primary"], s["preferred_mode"]
        assert p and p["native_resolution"]["w"] == native[0] and p["native_resolution"]["h"] == native[1], label
        assert (pref["w"], pref["h"], pref["hz"]) == want, (label, pref)
        assert p["refresh_rates"] == rates, (label, p["refresh_rates"])


@test
def D05_4k30_never_preferred_over_4k60_and_no_lower_resolution_for_refresh():
    s = _caps((3840, 2160, 30), (95, 97, 63), [(3840, 2160, 30), (3840, 2160, 60), (1920, 1080, 120)], 0)
    assert (s["preferred_mode"]["w"], s["preferred_mode"]["hz"]) == (3840, 60), s["preferred_mode"]
    codes = [f["code"] for f in s["findings"]]
    assert "REFRESH_BELOW_BEST" in codes, codes


@test
def D06_vrr_and_hdr_supported_vs_active():
    s = _caps((3840, 2160, 60), (97,), hdr=True, hf_vrr=(40, 120))
    p = s["primary"]
    assert p["vrr_supported"] is True and p["vrr_range"]["min"] == 40 and p["vrr_range"]["max"] == 120
    assert p["hdr_supported"] is True and p["hdr_active"] is None and p["vrr_active"] is None   # unknown without Steam
    s = _caps((3840, 2160, 60), (97,))
    assert s["primary"]["vrr_supported"] is False and s["primary"]["hdr_supported"] is False


@test
def D07_unknown_edid_is_safe_display_mode():
    box = Box()
    try:
        box.connector("HDMI-A-1", modes=[(1920, 1080)], edid_bytes=None)
        s = snap(box)
        assert s["safe_display_mode"] is True and s["primary"]["edid_ok"] is False
        assert s["primary"]["native_resolution"] == {"w": 1920, "h": 1080, "hz": None}       # kernel's first mode
        assert s["primary"]["vrr_supported"] is None and s["primary"]["hdr_supported"] is None
        assert any(f["code"] == "EDID_UNKNOWN" for f in s["findings"])
    finally:
        box.close()


@test
def D08_disconnected_and_display_changed():
    box = Box()
    try:
        box.connector("HDMI-A-1", status="disconnected")
        s = snap(box)
        assert s["primary"] is None and s["findings"][0]["code"] == "DISPLAY_DISCONNECTED" and s["safe_display_mode"]
        d = SD.Display(cef=None)
        os.environ["STONE_XWAYLAND_FAKE"] = "{}"
        d.cef = None
        a = SD.collect("native", use_steam=False)
        box.connector("HDMI-A-1", modes=[(3840, 2160)], edid_bytes=make_edid(vics=(97,)))
        b = SD.collect("native", use_steam=False)
        assert model.signature(a) != model.signature(b) and b["primary"]["native_resolution"]["w"] == 3840
        # cached until the display changes; the change drops the cache
        first = d.status()
        assert d.status() is first
        d._changed("test")
        assert d.status() is not first
    finally:
        box.close()


@test
def D09_multiple_outputs_primary_gaming_display():
    box = Box()
    try:
        box.connector("eDP-1", card="card0", modes=[(1280, 800)], edid_bytes=make_edid(native=(1920, 1080, 60), hdmi=False))
        box.connector("HDMI-A-1", modes=[(3840, 2160)], edid_bytes=make_edid(vics=(97,)))
        box.connector("DP-1", status="disconnected")
        s = snap(box)
        assert s["primary"]["connector"] == "HDMI-A-1", s["primary"]["connector"]
        assert len(s["displays"]) == 3 and sum(d["connected"] for d in s["displays"]) == 2
        # Steam says which one it shows on: that one wins
        st = FakeSteam({"modes": [(1920, 1080, 60)], "current": 0, "name": "eDP-1"})
        s = snap(box, st)
        assert s["primary"]["connector"] == "eDP-1", s["primary"]["connector"]
    finally:
        box.close()


@test
def D10_steam_state_decoding():
    raw = steam_state_bytes([(3840, 2160, 60), (1920, 1080, 120)], 0, default=(1920, 1080), native=(3840, 2160), vrr=True)
    st = steam.decode_state(raw)
    d = st["displays"][0]
    assert d["name"] == "HDMI-A-1" and d["current_mode_id"] == 0 and d["vrr_capable"] and not d["hdr_capable"]
    assert d["modes"][1] == {"id": 1, "w": 1920, "h": 1080, "hz": 120}
    assert st["game_default"] == {"w": 1920, "h": 1080} and st["game_native"] == {"w": 3840, "h": 2160}
    assert steam.parse_answer(None)[0] is None and steam.parse_answer('{"error":"x"}') == (None, "x")
    o = steam.from_object({"displays": [{"id": 1, "currentModeId": 0, "modes": [{"id": 0, "width": 2560, "height": 1440, "refreshHz": 144}]}],
                           "gameResolutionOverrideDefault": {"width": 1280, "height": 800}})
    assert o["displays"][0]["modes"][0] == {"id": 0, "w": 2560, "h": 1440, "hz": 144} and o["game_default"] == {"w": 1280, "h": 800}


@test
def D11_root_cause_steam_default_below_native_is_reported():
    """The board case: a 4K TV, output 4K60, Steam's default game size 1920x1080."""
    box = Box()
    try:
        box.connector("HDMI-A-1", modes=[(3840, 2160), (1920, 1080)], edid_bytes=make_edid(vics=(16, 97)))
        st = FakeSteam({"modes": [(3840, 2160, 60), (1920, 1080, 60)], "current": 0, "default": (1920, 1080), "native": (3840, 2160)})
        s = snap(box, st, xw={":0": [3840, 2160], ":1": [1920, 1080]}, policy="steam")
        codes = {f["code"]: f for f in s["findings"]}
        assert codes["STEAM_GAME_DEFAULT_BELOW_NATIVE"]["level"] == "warn", codes
        assert "GAME_SCREEN_BELOW_OUTPUT" in codes and "OUTPUT_NATIVE" in codes, codes
        assert s["output"] == {"w": 3840, "h": 2160, "hz": 60} and s["game_environment"]["w"] == 1920
        text = cli.status_text(s)
        for must in ("A · Nativa:          3840x2160@60", "B · Salida actual:   3840x2160@60",
                     "Steam por defecto: 1920x1080", ":1 1920x1080", "STEAM_GAME_DEFAULT_BELOW_NATIVE"):
            assert must in text, (must, text)
        s = snap(box, st, policy="native")
        assert s["game_environment"]["w"] == 3840 and s["game_environment"]["source"] == "policy: native"
    finally:
        box.close()


@test
def D12_output_below_native_reported_not_forced():
    box = Box()
    try:
        box.connector("HDMI-A-1", modes=[(3840, 2160), (1920, 1080)], edid_bytes=make_edid(vics=(16, 63, 97)))
        st = FakeSteam({"modes": [(3840, 2160, 60), (1920, 1080, 120)], "current": 1, "default": (1920, 1080)})
        s = snap(box, st)
        f = {x["code"]: x for x in s["findings"]}
        assert "OUTPUT_BELOW_NATIVE" in f and "1920x1080" in f["OUTPUT_BELOW_NATIVE"]["text"], f
        assert (s["preferred_mode"]["w"], s["preferred_mode"]["hz"]) == (3840, 60)
        assert not any(c[0] == "default" for c in st.calls)          # reading never changes anything
    finally:
        box.close()


@test
def D13_game_environment_default_raised_verified_or_rolled_back():
    box = Box()
    try:
        native = {"w": 3840, "h": 2160}
        st = FakeSteam({"modes": [(3840, 2160, 60)], "current": 0, "default": (1920, 1080)})
        env = SD.GameEnvironment(cef=st)

        def read():
            s, _ = steam.display_state(st)
            return s, native
        r = env.ensure_default(read, settle=lambda: None)
        assert r["status"] == "changed" and st.state["default"] == (3840, 2160), r
        assert env.ensure_default(read, settle=lambda: None)["status"] == "ok"
        assert env.restore_default()["status"] == "restored" and st.calls[-1] == ("default", 1920, 1080)
        # Steam ignores the call: STONE puts the old value back and does not try again
        st2 = FakeSteam({"modes": [(3840, 2160, 60)], "current": 0, "default": (1920, 1080)}, default_takes=False)
        env2 = SD.GameEnvironment(cef=st2)
        os.remove(env2.path)

        def read2():
            s, _ = steam.display_state(st2)
            return s, native
        r = env2.ensure_default(read2, settle=lambda: None)
        assert r["status"] == "unsupported" and st2.calls == [("default", 3840, 2160), ("default", 1920, 1080)], (r, st2.calls)
        assert env2.ensure_default(read2, settle=lambda: None)["status"] == "unsupported" and len(st2.calls) == 2
        # no Steam: nothing is touched
        os.remove(env2.path)
        assert SD.GameEnvironment(cef=FakeSteam(answer=False)).ensure_default(lambda: (None, native))["status"] == "unknown"
    finally:
        box.close()


@test
def D14_game_matrix_known_unknown_and_chosen_sizes():
    box = Box()
    try:
        cyberpunk, unknown, chosen, native_already, stone = 1091500, 3999990, 2000000, 2100000, 3141592653
        st = FakeSteam(overrides={chosen: "1280x720", native_already: "Native"})
        env = SD.GameEnvironment(cef=st)
        r = env.apply([cyberpunk, unknown, chosen, native_already, stone])
        assert sorted(r["changed"]) == sorted([cyberpunk, unknown, stone]), r
        assert st.over[cyberpunk] == "Native" and st.over[unknown] == "Native"     # no profile needed, no AppID special case
        assert st.over[chosen] == "1280x720"                                        # the person's choice is kept
        r = env.restore()
        assert sorted(r["changed"]) == sorted([cyberpunk, unknown, stone]) and st.over[native_already] == "Native"
        assert st.over[cyberpunk] == "Default"
        # many games go in batches
        many = list(range(10, 10 + 400))
        st3 = FakeSteam()
        SD.GameEnvironment(cef=st3).apply(many)
        assert len([c for c in st3.calls if c[0] == "apps"]) == 3
    finally:
        box.close()


@test
def D15_no_production_fakes_and_no_cyberpunk_special_case():
    src = os.path.join(REPO, "system_files", "usr", "lib", "stone")
    for dirpath, _, files in os.walk(src):
        for f in files:
            if f.endswith(".py"):
                text = open(os.path.join(dirpath, f)).read()
                assert "1091500" not in text and "cyberpunk" not in text.lower(), f
    # the only test hook in production code: STONE_XWAYLAND_FAKE, read from the environment
    text = open(os.path.join(src, "stone_display", "xwayland.py")).read()
    assert text.count("STONE_XWAYLAND_FAKE") == 2


@test
def D16_kernel_connectors_names_not_assumed():
    box = Box()
    try:
        box.connector("DP-3", card="card2", modes=[(2560, 1440), (2560, 1440), (1920, 1080)], edid_bytes=make_edid(native=(2560, 1440, 60), hdmi=True))
        c = drm.connectors()
        assert c[0]["connector"] == "DP-3" and c[0]["kind"] == "DisplayPort" and len(c[0]["modes"]) == 2
        s = snap(box)
        assert s["primary"]["connection_type"] == "DisplayPort → HDMI (adapter)"
    finally:
        box.close()


# ---------------------------------------------------------------- rc18: link budget, Steam pages, set-output
def _mode(w, h, hz):
    return {"w": w, "h": h, "hz": hz}


def _cap(modes, tmds=None, frl=None, source="kernel+edid"):
    return {"available_modes": modes, "hdmi_max_tmds_mhz": tmds, "hdmi_frl_gbps": frl, "modes_source": source}


ALL = [_mode(3840, 2160, 60), _mode(3840, 2160, 30), _mode(2560, 1440, 60), _mode(1920, 1080, 120),
       _mode(1920, 1080, 60), _mode(3840, 2160, 120)]


@test
def D17_link_300mhz_cannot_carry_4k60_and_says_what_to_buy():
    from stone_display import link
    p = link.plan(_cap(ALL, tmds=300))
    assert not p["reaches_4k60"] and p["display_offers_4k60"] and p["limited_by_link"], p
    assert (p["best_fluid"]["w"], p["best_fluid"]["hz"]) == (2560, 60), p["best_fluid"]       # 1440p60, not 4K30
    assert (p["best_max_res"]["w"], p["best_max_res"]["hz"]) == (3840, 30), p["best_max_res"]  # 4K30 carries 297 MHz
    assert "DisplayPort 1.4" in p["advice"] and "4K 60" in p["advice"], p["advice"]
    assert link.plan(_cap(ALL, tmds=300), "max_res")["target"]["hz"] == 30
    assert link.plan(_cap(ALL, tmds=300))["target"]["h"] == 1440


@test
def D18_link_600mhz_and_frl_reach_4k60_and_4k120():
    from stone_display import link
    assert link.plan(_cap(ALL, tmds=600))["reaches_4k60"]
    p = link.plan(_cap(ALL, tmds=600))
    assert (p["best_fluid"]["w"], p["best_fluid"]["hz"]) == (3840, 60) and p["advice"] is None, p
    q = link.plan(_cap(ALL, frl=40))
    assert (q["best_fluid"]["w"], q["best_fluid"]["hz"]) == (3840, 120), q["best_fluid"]
    assert link.plan(_cap(ALL, frl=18))["best_fluid"]["hz"] == 60


@test
def D19_link_without_figures_trusts_the_modes_and_steam_is_not_filtered():
    from stone_display import link
    assert link.plan(_cap(ALL))["budget_mhz"] is None and link.plan(_cap(ALL))["best_fluid"]["hz"] == 120
    st = link.plan(_cap([_mode(3840, 2160, 60), _mode(2560, 1440, 60)], tmds=300, source="steam"))
    assert st["reaches_4k60"] and st["budget_mhz"] is None, st          # Steam validated them on this link
    assert link.plan(_cap([])) is None
    assert link.plan(_cap([_mode(1920, 1080, None)], source="steam")) is None
    k = link.plan(_cap([_mode(2560, 1440, None)]))                  # kernel accepts it, EDID names no rate
    assert k["best_fluid"]["hz"] == 60 and k["best_fluid"]["hz_assumed"], k


@test
def D20_games_see_the_best_the_link_carries_not_an_unreachable_native():
    box = Box()
    try:
        box.connector("HDMI-A-1", modes=[(3840, 2160), (2560, 1440), (1920, 1080)],
                      edid_bytes=make_edid(native=(3840, 2160, 60), vics=(95, 97, 16), tmds=300))
        s = snap(box)
    finally:
        box.close()
    assert s["primary"]["native_resolution"]["w"] == 3840, s["primary"]["native_resolution"]
    assert s["link"] and not s["link"]["reaches_4k60"], s["link"]
    env = s["game_environment"]
    assert (env["w"], env["h"]) == (2560, 1440) and "link" in env["source"], env
    assert any(f["code"] == "LINK_LIMITS_MODES" for f in s["findings"]), s["findings"]


@test
def D21_steam_pages_all_tried_and_the_error_names_each():
    from stone_home import steamcef
    pages = [{"title": "Screensaver_uid2", "url": "https://steamloopback.host/x", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/a"},
             {"title": "SharedJSContext", "url": "https://steamloopback.host/y", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/b"},
             {"title": "other", "url": "about:blank", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/c"},
             {"title": "nows", "url": "about:blank"}]
    c = steamcef.candidates(pages)
    assert [p["title"] for p in c] == ["SharedJSContext", "Screensaver_uid2", "other"], c
    assert steamcef.pick([pages[0]])["title"] == "Screensaver_uid2"          # steamloopback page, as before
    assert steamcef.pick([pages[2]]) is None

    class Cef:
        def ui_values(self, expr):
            return [("SharedJSContext", None, "RuntimeError: boom"),
                    ("Screensaver_uid2", json.dumps({"error": "no DisplayManager"}), None)]
    st, err = steam.display_state(Cef())
    assert st is None and "SharedJSContext: RuntimeError: boom" in err and "Screensaver_uid2: no DisplayManager" in err, err

    class Cef2:
        def ui_values(self, expr):
            return [("a", json.dumps({"error": "no DisplayManager"}), None),
                    ("b", json.dumps({"b64": base64.b64encode(steam_state_bytes([(2560, 1440, 60)], 0)).decode()}), None)]
    st, err = steam.display_state(Cef2())
    assert err is None and st["displays"] and st["displays"][0]["modes"], (st, err)


@test
def D22_set_output_only_reachable_modes_and_reset_removes_it():
    import io
    home = tempfile.mkdtemp()
    old = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        box = Box()
        try:
            box.connector("HDMI-A-1", modes=[(3840, 2160), (2560, 1440), (1920, 1080)],
                          edid_bytes=make_edid(native=(3840, 2160, 60), vics=(95, 97, 16), tmds=300))
            s = snap(box)
        finally:
            box.close()
        out = io.StringIO()
        out = io.StringIO()
        assert cli.set_output("5120x2880", s, out) == 1 and "No se cambió nada" in out.getvalue()
        assert cli.set_output("banana", s, io.StringIO()) == 2
        out = io.StringIO()
        assert cli.set_output("2560x1440", s, out) == 0
        path = os.path.expanduser(cli.ENV_FILE)
        txt = open(path).read()
        assert "SCREEN_WIDTH=2560" in txt and "SCREEN_HEIGHT=1440" in txt and "stonectl display reset" in out.getvalue()
        out = io.StringIO()
        assert cli.main(["reset"], out) == 0 and not os.path.exists(path)
        assert cli.set_output("2560x1440", {"primary": None, "link": None}, io.StringIO()) == 1     # no data: nothing forced
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old
        shutil.rmtree(home, ignore_errors=True)


@test
def D23_steam_settings_are_built_from_what_steam_offers_and_stay_safe():
    from stone_home import steamsettings as SS
    tree = {"System": {"Display": {"GetBrightness": "f0", "SetBrightness": "f1", "GetHdrEnabled": "f0", "SetHdrEnabled": "f1"},
                       "Audio": {"GetMute": "f0", "SetMute": "f1"}, "Account": {"GetEmail": "f0", "SetEmail": "f1"},
                       "GetShutdownDelay": "f0", "GetNoArgsOnly": "f2"},
            "Apps": {"GetAppOverview": "f1", "GetLanguage": "f0"}}
    ids = {c["id"]: c for c in SS.catalog(tree)}
    assert set(ids) == {"System.Display.Brightness", "System.Display.HdrEnabled", "System.Audio.Mute", "Apps.Language"}, set(ids)
    assert ids["System.Audio.Mute"]["set_path"] == ["System", "Audio", "SetMute"] and ids["Apps.Language"]["set_path"] is None
    assert ids["System.Display.HdrEnabled"]["label"] == "Hdr Enabled" and ids["System.Display.HdrEnabled"]["area"] == "Display"
    assert SS.catalog({"A": {"Get$x": "f0"}}) == [] and SS.catalog({"System": {"GetOk;x": "f0"}}) == []

    class Cef:
        def __init__(self):
            self.written = []

        def probe(self):
            return [{"title": "x", "has_steamclient": False, "tree": {}}, {"title": "SharedJSContext", "has_steamclient": True, "tree": tree}]

        def ui_value(self, expr):
            if "setTimeout" in expr:                                   # the batched read
                return json.dumps({"System.Display.Brightness": 70, "System.Display.HdrEnabled": True,
                                   "System.Audio.Mute": False, "Apps.Language": "spanish"})
            self.written.append(expr)
            return "ok"
    home = tempfile.mkdtemp()
    old = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = home
    try:
        cef = Cef()
        ss = SS.SteamSettings(cef)
        r = ss.read()
        by = {x["id"]: x for x in r["settings"]}
        assert r["available"] and by["System.Audio.Mute"]["writable"] and by["System.Audio.Mute"]["type"] == "bool"
        assert not by["System.Display.Brightness"]["writable"]            # numbers are shown, not changed
        assert not by["Apps.Language"]["writable"] and by["Apps.Language"]["type"] == "text"
        assert os.path.isfile(SS.state_path())                             # the list of functions is kept
        assert ss.set_bool("System.Audio.Mute", True)["ok"] and "SetMute" in cef.written[-1] and "true" in cef.written[-1]
        for bad in (("System.Display.Brightness", True), ("Account.Email", True), ("../x", True), ("System.Audio.Mute", 1)):
            try:
                ss.set_bool(*bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        os.remove(SS.state_path())
        assert SS.SteamSettings(type("E", (), {"probe": lambda self: [], "ui_value": lambda self, e: None})()).read()["available"] is False
    finally:
        if old is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = old
        shutil.rmtree(home, ignore_errors=True)


@test
def D24_steam_settings_are_shown_in_spanish_only_where_known():
    from stone_home import steamsettings as SS
    assert SS.spanish("Enable Hdr", "Display") == ("Activar HDR", "Pantalla")
    assert SS.spanish("Some Unknown Thing", "Weird › Audio") == ("Some Unknown Thing", "Weird › Audio")


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
