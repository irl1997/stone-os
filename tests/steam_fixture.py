"""A small fake Steam folder for STONE Home tests: two library folders, installed games,
tools that must not be listed, play time, a non-Steam shortcut, the STONE shortcut, and
artwork in both of Steam's cache layouts. Fictional game names and AppIDs."""

import os
import struct
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "system_files", "usr", "lib", "stone"))

from stone_home import shortcuts as SC  # noqa: E402
from stone_home import vdf  # noqa: E402

ACCOUNT = "12345678"
EMU_APPID = 0x8ABC1234


def png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def acf(appid, name, flags=4, size=12_000_000_000, last=0):
    return f'''"AppState"
{{
	"appid"		"{appid}"
	"Universe"		"1"
	"name"		"{name}"
	"StateFlags"		"{flags}"
	"installdir"		"{name}"
	"LastUpdated"		"1760000000"
	"LastPlayed"		"{last}"
	"SizeOnDisk"		"{size}"
}}
'''


def build(root, with_stone_shortcut=True, now=None):
    now = int(now or time.time())
    lib2 = os.path.join(root, "lib2")
    _w(os.path.join(root, "steamapps", "libraryfolders.vdf"), f'''"libraryfolders"
{{
	"0"
	{{
		"path"		"{root}"
		"apps" {{ "1001" "1" "228980" "1" }}
	}}
	"1"
	{{
		"path"		"{lib2}"
	}}
}}
''')
    _w(os.path.join(root, "steamapps", "appmanifest_1001.acf"), acf(1001, "Marea Lenta", last=now - 3600))
    _w(os.path.join(root, "steamapps", "appmanifest_228980.acf"), acf(228980, "Steamworks Common Redistributables"))
    _w(os.path.join(root, "steamapps", "appmanifest_9999001.acf"), acf(9999001, "Proton 9.0"))
    _w(os.path.join(lib2, "steamapps", "appmanifest_1002.acf"), acf(1002, "Basalto <b>&</b>", size=800_000_000, last=0))
    _w(os.path.join(lib2, "steamapps", "appmanifest_1003.acf"), acf(1003, "Ceniza", flags=6, last=now - 86400 * 40))
    cfg = os.path.join(root, "userdata", ACCOUNT, "config")
    _w(os.path.join(cfg, "localconfig.vdf"), f'''"UserLocalConfigStore"
{{
	"Software" {{ "Valve" {{ "Steam" {{ "apps" {{
		"1001" {{ "LastPlayed" "{now - 600}" "Playtime" "2520" }}
		"1002" {{ "LastPlayed" "{now - 86400 * 2}" "Playtime" "30" }}
	}} }} }} }}
}}
''')
    items = {"0": {"appid": EMU_APPID, "AppName": "Órbita Norte", "Exe": '"/usr/bin/flatpak"', "StartDir": '"/usr/bin/"',
                   "LaunchOptions": "run org.example.Emu", "IsHidden": 0, "LastPlayTime": now - 86400 * 5, "tags": {}}}
    if with_stone_shortcut:
        items["1"] = SC.entry(SC.short_appid())
    _w(os.path.join(cfg, "shortcuts.vdf"), vdf.binary_dumps({"shortcuts": items}))
    cache = os.path.join(root, "appcache", "librarycache")
    _w(os.path.join(cache, "1001_library_600x900.jpg"), png(60, 90, (196, 122, 28)))
    _w(os.path.join(cache, "1001_library_hero.jpg"), png(192, 62, (138, 135, 112)))
    _w(os.path.join(cache, "1002", "library_hero.jpg"), png(192, 62, (27, 34, 48)))
    _w(os.path.join(cache, "1002", "a1b2c3", "library_600x900.jpg"), png(60, 90, (77, 90, 107)))
    _w(os.path.join(cfg, "grid", f"{EMU_APPID}p.png"), png(60, 90, (10, 10, 18)))
    return root
