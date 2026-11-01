"""EDID (VESA E-EDID 1.3/1.4 base block + CTA-861 extensions): what the display says it can do.

Only fields that are read reliably from the bytes are returned; anything absent or malformed
is None (UNKNOWN). The serial number is never returned (privacy: diagnostics are shared).

    parse(bytes) -> dict | None      None when the data is not an EDID at all

Fields:
  vendor, product_code, name, year           identity (no serial)
  preferred        {"w","h","hz"}            first detailed timing (the display's native mode)
  timings          [{"w","h","hz"}]          every detailed timing (base block and CTA)
  vics             [{"vic","w","h","hz"}]    CTA-861 video modes the display lists (known VICs)
  range_hz         (min, max) or None        display range limits descriptor (vertical)
  vrr              {"min","max","source"}    variable refresh: HDMI Forum VSDB (HDMI 2.1 VRR),
                                             AMD FreeSync VSDB, or the range limits descriptor
                                             with "continuous frequency" set (EDID 1.4)
  hdr              {"eotfs": [...], "max_luminance_nits"} or None   CTA HDR static metadata
  hdmi_max_tmds_mhz, hdmi_frl_gbps           what the HDMI sink accepts (None if not HDMI)
"""

# CTA-861 VICs that matter for a TV console (width, height, refresh). Unknown VICs are skipped.
VIC = {
    1: (640, 480, 60), 2: (720, 480, 60), 3: (720, 480, 60), 4: (1280, 720, 60), 5: (1920, 1080, 60),
    16: (1920, 1080, 60), 19: (1280, 720, 50), 20: (1920, 1080, 50), 31: (1920, 1080, 50),
    32: (1920, 1080, 24), 33: (1920, 1080, 25), 34: (1920, 1080, 30), 47: (1280, 720, 120),
    63: (1920, 1080, 120), 64: (1920, 1080, 100), 93: (3840, 2160, 24), 94: (3840, 2160, 25),
    95: (3840, 2160, 30), 96: (3840, 2160, 50), 97: (3840, 2160, 60), 98: (4096, 2160, 24),
    99: (4096, 2160, 25), 100: (4096, 2160, 30), 101: (4096, 2160, 50), 102: (4096, 2160, 60),
    103: (3840, 2160, 24), 104: (3840, 2160, 25), 105: (3840, 2160, 30), 106: (3840, 2160, 50),
    107: (3840, 2160, 60), 117: (3840, 2160, 100), 118: (3840, 2160, 120), 119: (3840, 2160, 100),
    120: (3840, 2160, 120), 218: (4096, 2160, 100), 219: (4096, 2160, 120),
}
HEADER = bytes([0, 255, 255, 255, 255, 255, 255, 0])
EOTF_NAMES = {0: "SDR", 1: "HDR (gamma)", 2: "HDR10 (PQ)", 3: "HLG"}
OUI_HDMI = 0x000C03          # HDMI 1.4 VSDB (IEEE OUI, little endian in the block)
OUI_HDMI_FORUM = 0xC45DD8    # HDMI Forum VSDB (HDMI 2.x)
OUI_AMD = 0x00001A           # AMD FreeSync VSDB


def _checksum_ok(block):
    return len(block) == 128 and sum(block) % 256 == 0


def _dtd(d):
    """Detailed timing descriptor (18 bytes) -> {"w","h","hz"} or None."""
    clock = (d[0] | d[1] << 8) * 10_000          # Hz
    if clock == 0:
        return None
    hact = d[2] | (d[4] & 0xF0) << 4
    hblank = d[3] | (d[4] & 0x0F) << 8
    vact = d[5] | (d[7] & 0xF0) << 4
    vblank = d[6] | (d[7] & 0x0F) << 8
    interlaced = bool(d[17] & 0x80)
    if hact == 0 or vact == 0:
        return None
    total = (hact + hblank) * (vact + vblank)
    if total == 0:
        return None
    hz = clock / total
    if interlaced:
        vact *= 2
        hz *= 2
    return {"w": hact, "h": vact, "hz": round(hz, 2)}


def _vendor(b):
    v = b[8] << 8 | b[9]
    letters = [(v >> 10) & 31, (v >> 5) & 31, v & 31]
    if any(x < 1 or x > 26 for x in letters):
        return None
    return "".join(chr(64 + x) for x in letters)


def _text(d):
    t = bytes(d[5:18]).split(b"\n")[0].decode("ascii", "replace").strip()
    return "".join(c for c in t if c.isprintable()) or None


def _cta(block, out):
    if block[0] != 0x02 or block[1] < 1:
        return
    dtd_start = block[2]
    if not 4 <= dtd_start <= 127:
        dtd_start = 127 if dtd_start == 0 else dtd_start
    i = 4
    while i < min(dtd_start, 127):
        tag, length = block[i] >> 5, block[i] & 31
        data = block[i + 1:i + 1 + length]
        if len(data) < length:
            break
        if tag == 2:                                   # video data block
            for sv in data:
                vic = sv & 0x7F if (sv & 0x7F) <= 64 and sv & 0x80 else sv
                if vic in VIC:
                    w, h, hz = VIC[vic]
                    out["vics"].append({"vic": vic, "w": w, "h": h, "hz": hz})
        elif tag == 3 and length >= 3:                 # vendor specific data block
            oui = data[0] | data[1] << 8 | data[2] << 16
            if oui == OUI_HDMI:
                out["is_hdmi"] = True
                if length >= 7 and data[6]:
                    out["hdmi_max_tmds_mhz"] = data[6] * 5
            elif oui == OUI_HDMI_FORUM and length >= 5:
                out["is_hdmi"] = True
                if data[4]:
                    out["hdmi_max_tmds_mhz"] = max(out.get("hdmi_max_tmds_mhz") or 0, data[4] * 5)
                if length >= 7:
                    frl = (data[6] >> 4) & 0x0F
                    out["hdmi_frl_gbps"] = {1: 9, 2: 18, 3: 24, 4: 32, 5: 40, 6: 48}.get(frl)
                if length >= 10:                       # VRRmin (6 bits), VRRmax (10 bits)
                    vmin = data[8] & 0x3F
                    vmax = ((data[8] & 0xC0) << 2) | data[9]
                    if vmin and vmax > vmin:
                        out["vrr"] = {"min": vmin, "max": vmax, "source": "HDMI 2.1 VRR"}
            elif oui == OUI_AMD and length >= 7:
                vmin, vmax = data[5], data[6]
                if vmin and vmax > vmin and not out.get("vrr"):
                    out["vrr"] = {"min": vmin, "max": vmax, "source": "FreeSync"}
        elif tag == 7 and length >= 1:                 # extended tag
            ext = data[0]
            if ext == 6 and length >= 3:               # HDR static metadata
                eotfs = [EOTF_NAMES[k] for k in range(4) if data[1] >> k & 1]
                lum = None
                if length >= 4 and data[3]:
                    lum = round(50 * 2 ** (data[3] / 32))
                out["hdr"] = {"eotfs": eotfs, "max_luminance_nits": lum}
            elif ext == 0 and length >= 2:             # video capability: nothing needed
                pass
        i += 1 + length
    j = dtd_start
    while 4 <= j and j + 18 <= 127:
        t = _dtd(block[j:j + 18])
        if not t:
            break
        out["timings"].append(t)
        j += 18


def parse(data):
    if not data or len(data) < 128 or bytes(data[:8]) != HEADER:
        return None
    b = bytes(data)
    out = {"vendor": _vendor(b), "product_code": b[10] | b[11] << 8, "name": None,
           "year": (1990 + b[17]) if 0 < b[17] < 0xFF else None, "edid_version": f"{b[18]}.{b[19]}",
           "preferred": None, "timings": [], "vics": [], "range_hz": None, "vrr": None, "hdr": None,
           "hdmi_max_tmds_mhz": None, "hdmi_frl_gbps": None, "is_hdmi": False,
           "checksum_ok": _checksum_ok(b[:128])}
    continuous = bool(b[24] & 0x01) and b[19] >= 4
    for k in range(4):
        d = b[54 + 18 * k:72 + 18 * k]
        if d[0] or d[1]:
            t = _dtd(d)
            if t:
                out["timings"].append(t)
                if out["preferred"] is None and k == 0:
                    out["preferred"] = t
            continue
        if d[3] == 0xFC:
            out["name"] = _text(d)
        elif d[3] == 0xFD:
            vmin, vmax = d[5], d[6]
            if d[4] & 0x01:
                vmax += 255
            if d[4] & 0x02:
                vmin += 255
            if vmin and vmax >= vmin:
                out["range_hz"] = (vmin, vmax)
    n_ext = b[126]
    for e in range(1, n_ext + 1):
        blk = b[128 * e:128 * (e + 1)]
        if len(blk) < 128:
            break
        _cta(blk, out)
    if out["vrr"] is None and continuous and out["range_hz"] and out["range_hz"][1] > out["range_hz"][0]:
        out["vrr"] = {"min": out["range_hz"][0], "max": out["range_hz"][1], "source": "EDID range (continuous)"}
    return out
