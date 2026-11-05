"""STONE's view of Steam's settings, built from what THIS Steam really offers.

Steam's interface (the CEF page, see steamcef.py) exposes SteamClient.<area>.<Function>. STONE does not
assume any of them: it walks the list of functions Steam reports (steamcef.probe, names only) and builds
the catalog from the pairs it finds:

    GetFoo() + SetFoo(value)   -> a setting STONE can show and, for true/false values, change
    GetFoo()                   -> a setting STONE can show

A setting is kept only if its current value is a boolean, a number or a short text. Names that touch
accounts, purchases, installs, deletions, resets, power or sign-in are never read or written. Changing is
limited to true/false values, set to the same type that was read, and only for ids that are in the catalog.
The names are Steam's own (shown split into words); curated, translated controls come on top of this.
"""

import json
import os
import re
import time

from . import steamcef

DENY = re.compile(r"(account|login|logon|logout|password|token|purchase|payment|wallet|billing|email|auth|"
                  r"friend|chat|cloud|install|uninstall|delete|remove|reset|shutdown|reboot|restart|suspend|"
                  r"update|download|cookie|session|steamid|secret|cert|factory|format|wipe|clear|crash|"
                  r"debug|developer|beta|branch|login|signin|signout|gift|market|inventory|key)", re.I)
IDENT = re.compile(r"^[A-Za-z0-9_]+$")
MAX_ITEMS = 160
CACHE_S = 600


def state_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "stone", "steam-api.json")


def humanize(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).strip()


# Spanish for the names STONE recognises. Steam's own name stays when it is not here: nothing is guessed.
AREAS_ES = {"Display": "Pantalla", "Audio": "Audio", "Controller": "Mandos", "Network": "Red", "Bluetooth": "Bluetooth",
            "Downloads": "Descargas", "Interface": "Interfaz", "Compatibility": "Compatibilidad", "System": "Sistema",
            "Notifications": "Notificaciones", "Storage": "Almacenamiento", "Power": "Energía", "Input": "Entrada"}
LABELS_ES = {"Enable Hdr": "Activar HDR", "Use Native Resolution": "Usar la resolución nativa", "Screen Brightness": "Brillo de pantalla",
             "Game Volume": "Volumen de los juegos", "Mute On Minimize": "Silenciar al minimizar", "Enable Xbox Support": "Soporte para mandos Xbox",
             "Enable Playstation Support": "Soporte para mandos PlayStation", "Haptics Enabled": "Vibración háptica",
             "Enable Wifi Power Save": "Ahorro de energía del Wi-Fi", "Allow Downloads During Gameplay": "Descargar mientras se juega",
             "Download Region": "Región de descarga", "Show Fps Counter": "Mostrar contador de FPS", "Enable Animations": "Animaciones",
             "Enable Steam Play": "Activar Steam Play", "Enable Developer Mode": "Modo desarrollador",
             "Show Toast Notifications": "Mostrar avisos emergentes"}


def spanish(label, area):
    """(label, area) in Spanish where known; the person sees Steam's own words otherwise."""
    return LABELS_ES.get(label, label), " › ".join(AREAS_ES.get(a, a) for a in area.split(" › "))


def catalog(tree):
    """[{id, path, get_path, set_path|None, label, area}] from a probe tree (functions are 'f<arity>')."""
    out = []

    def walk(node, path):
        if not isinstance(node, dict):
            return
        for k, v in sorted(node.items()):
            if isinstance(v, dict):
                walk(v, path + [k])
        for k, v in sorted(node.items()):
            if not (isinstance(v, str) and v == "f0" and k.startswith("Get") and len(k) > 3):
                continue
            base = k[3:]
            full = ".".join(path + [k])
            if not all(IDENT.match(x) for x in path + [k]) or DENY.search(full):
                continue
            setter = node.get("Set" + base)
            out.append({"id": ".".join(path + [base]), "get_path": path + [k],
                        "set_path": path + ["Set" + base] if isinstance(setter, str) and setter == "f1" else None,
                        "label": humanize(base), "area": " › ".join(path[1:] if path and path[0] == "System" else path) or "Steam"})
    walk(tree, [])
    return out[:MAX_ITEMS]


def _read_expression(items):
    pairs = json.dumps([[c["id"], c["get_path"]] for c in items])
    return ("(async()=>{const R=window.SteamClient;const out={};"
            f"for(const [id,path] of {pairs}){{try{{let o=R;for(const k of path.slice(0,-1))o=o[k];const f=o[path[path.length-1]];"
            "const v=await Promise.race([Promise.resolve(f.call(o)),new Promise((_,j)=>setTimeout(()=>j('t'),1000))]);"
            "out[id]=(typeof v==='boolean'||typeof v==='number'||(typeof v==='string'&&v.length<=80))?v:null;}catch(e){out[id]=null;}}"
            "return JSON.stringify(out);})()")


def _write_expression(c, value):
    if not isinstance(value, bool):
        raise ValueError("only true/false can be changed")
    path = json.dumps(c["set_path"])
    return ("(async()=>{const R=window.SteamClient;try{let o=R;const path=" + path + ";for(const k of path.slice(0,-1))o=o[k];"
            f"await Promise.resolve(o[path[path.length-1]]({json.dumps(value)}));return 'ok';}}catch(e){{return 'error';}}}})()")


class SteamSettings:
    def __init__(self, cef=steamcef):
        self.cef = cef
        self._cat = None
        self._t = 0
        self._bool = set()                  # ids whose last value read was true/false: the only ones that can be changed

    def tree(self, refresh=False):
        p = state_path()
        if not refresh:
            try:
                with open(p) as f:
                    d = json.load(f)
                if time.time() - d.get("time", 0) < 7 * 86400 and d.get("tree"):
                    return d["tree"]
            except (OSError, ValueError):
                pass
        best = None
        for row in self.cef.probe():
            t = row.get("tree") or {}
            if row.get("has_steamclient") and (best is None or len(json.dumps(t)) > len(json.dumps(best))):
                best = t
        if best:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                json.dump({"time": int(time.time()), "tree": best}, f)
        return best

    def catalog(self, refresh=False):
        if self._cat is None or refresh:
            t = self.tree(refresh)
            self._cat = catalog(t) if t else []
        return self._cat

    def read(self, refresh=False):
        """[{id, label, area, value, writable}] with current values; unreadable ones are left out."""
        cat = self.catalog(refresh)
        if not cat:
            return {"available": False, "settings": [], "error": "Steam no entregó su lista de funciones (¿Game Mode con Steam abierto?)"}
        raw = self.cef.ui_value(_read_expression(cat))
        try:
            vals = json.loads(raw) if isinstance(raw, str) else {}
        except ValueError:
            vals = {}
        out = []
        self._bool = set()
        for c in cat:
            v = vals.get(c["id"])
            if v is None:
                continue
            if isinstance(v, bool):
                self._bool.add(c["id"])
            label, area = spanish(c["label"], c["area"])
            out.append({"id": c["id"], "label": label, "label_en": c["label"], "area": area, "value": v,
                        "type": "bool" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "text",
                        "writable": bool(c["set_path"]) and isinstance(v, bool)})
        return {"available": True, "settings": out, "count": len(cat), "error": None}

    def set_bool(self, sid, value):
        c = next((x for x in self.catalog() if x["id"] == sid), None)
        if not c or not c["set_path"] or sid not in self._bool:
            raise ValueError("unknown, read-only or not a true/false setting")
        res = self.cef.ui_value(_write_expression(c, value))
        return {"ok": res == "ok", "id": sid, "value": value}
