"""STONE GAME PROFILE database: three layers, versioned, local.

  built-in   /usr/share/stone/game-profiles/steam/<appid>.json   shipped with the image (read only);
             index.json carries "db_version". A future STONE PROFILE DATABASE arrives the same
             way, with image updates (or a signed download into the same layout): no server now.
  auto       ~/.local/share/stone/game-profiles/auto/<appid>.json   measured on this console
  user       ~/.local/share/stone/game-profiles/user/<appid>.json   the person's own choices

Reading merges them per field: user > auto > built-in. A broken file is ignored (and reported),
never trusted. The launcher prefix ("steam") keeps room for Heroic, GOG or Epic later.
"""

import json
import os

from . import schema

BUILTIN = os.environ.get("STONE_GAME_PROFILES", "/usr/share/stone/game-profiles")
LAUNCHER = "steam"


def data_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "stone", "game-profiles")


def _path(layer, appid):
    if not str(appid).isdigit():
        raise schema.ProfileError("appid")
    base = os.path.join(BUILTIN, LAUNCHER) if layer == "builtin" else os.path.join(data_dir(), layer)
    return os.path.join(base, f"{appid}.json")


def db_version():
    try:
        with open(os.path.join(BUILTIN, "index.json")) as f:
            return int(json.load(f).get("db_version", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def read_layer(layer, appid):
    """(profile or None, error or None)."""
    try:
        with open(_path(layer, appid)) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"{layer}: unreadable ({e.__class__.__name__})"
    try:
        return schema.clean_profile(raw, layer), None
    except schema.ProfileError as e:
        return None, f"{layer}: {e}"


def write_layer(layer, profile):
    if layer not in ("auto", "user"):
        raise schema.ProfileError("only auto and user profiles are written")
    p = schema.clean_profile(dict(profile, schema=schema.PROFILE_VERSION), layer)
    path = _path(layer, p["appid"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(p, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return p


def delete_layer(layer, appid):
    try:
        os.remove(_path(layer, appid))
        return True
    except FileNotFoundError:
        return False


def load(appid, name=""):
    """The merged profile, with "layers" (which exist) and "errors" (files ignored)."""
    layers, errors = {}, []
    for layer in ("builtin", "auto", "user"):
        p, err = read_layer(layer, appid)
        if p:
            layers[layer] = p
        if err:
            errors.append(err)
    out = schema.empty_profile(appid, name)
    for layer in ("builtin", "auto", "user"):
        p = layers.get(layer)
        if not p:
            continue
        out["name"] = p["name"] or out["name"]
        out["status"] = p["status"]
        out["source"] = layer
        out["revision"] = p["revision"]
        for k, v in p["game"].items():
            if v not in (None, []):
                out["game"][k] = v
        out["modes"].update(p["modes"])
        if p["candidates"]:
            out["candidates"] = p["candidates"]
        if layer == "auto":
            out["measurements"] = p["measurements"]
            out["invalidation"] = p["invalidation"]
            out["failures"] = p["failures"]
            out["safety"] = p["safety"]
            out["verified"] = p["verified"]
    out["layers"] = sorted(layers)
    out["errors"] = errors
    out["db_version"] = db_version()
    return out
