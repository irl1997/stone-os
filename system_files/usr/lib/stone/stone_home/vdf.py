"""Valve KeyValues ("VDF") readers and the binary writer STONE Home needs.

Text VDF: libraryfolders.vdf, appmanifest_*.acf, localconfig.vdf.
Binary VDF: userdata/<id>/config/shortcuts.vdf (non-Steam shortcuts).

Both readers are defensive: Steam's files belong to the user and may be half-written or
damaged. A parse error raises VDFError; callers skip that file instead of crashing.
"""

import struct


class VDFError(ValueError):
    pass


# ---- text ------------------------------------------------------------------------------
def _tokens(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    out.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, nxt))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i >= n:
                raise VDFError("unterminated string")
            i += 1
            yield "s", "".join(out)
        elif c == "[":
            # conditional like [$WIN32]: ignored
            j = text.find("]", i)
            if j < 0:
                raise VDFError("unterminated condition")
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield "s", text[i:j]
            i = j


def loads(text):
    """Text VDF -> nested dicts of str. Duplicate keys: the last one wins."""
    root, stack, key = {}, [], None
    cur = root
    for tok in _tokens(text):
        if tok == "{":
            if key is None:
                raise VDFError("block without a key")
            new = {}
            cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif tok == "}":
            if not stack or key is not None:
                raise VDFError("unbalanced block")
            cur = stack.pop()
        else:
            if key is None:
                key = tok[1]
            else:
                cur[key] = tok[1]
                key = None
    if stack or key is not None:
        raise VDFError("unexpected end of file")
    return root


def load_file(path, limit=8 * 2**20):
    with open(path, "rb") as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise VDFError("file too large")
    return loads(raw.decode("utf-8", "replace"))


def get_ci(d, *keys):
    """Case-insensitive nested lookup; Steam is not consistent with key case."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        hit = None
        for kk, v in cur.items():
            if kk.lower() == k.lower():
                hit = v
        cur = hit
        if cur is None:
            return None
    return cur


# ---- binary ----------------------------------------------------------------------------
T_MAP, T_STR, T_INT, T_END = 0x00, 0x01, 0x02, 0x08


def _cstr(data, i):
    j = data.find(b"\0", i)
    if j < 0:
        raise VDFError("unterminated string")
    return data[i:j].decode("utf-8", "replace"), j + 1


def binary_loads(data):
    def parse_map(i):
        out = {}
        while True:
            if i >= len(data):
                raise VDFError("unexpected end of file")
            t = data[i]
            i += 1
            if t == T_END:
                return out, i
            key, i = _cstr(data, i)
            if t == T_MAP:
                out[key], i = parse_map(i)
            elif t == T_STR:
                out[key], i = _cstr(data, i)
            elif t == T_INT:
                if i + 4 > len(data):
                    raise VDFError("truncated int")
                out[key] = struct.unpack_from("<I", data, i)[0]
                i += 4
            else:
                raise VDFError(f"unknown type 0x{t:02x}")

    result, end = parse_map(0)
    # the file ends with the root map's T_END plus (usually) one more T_END
    if data[end:].strip(b"\x08") not in (b"",):
        raise VDFError("trailing data")
    return result


def binary_dumps(obj):
    out = bytearray()

    def put(d):
        for k, v in d.items():
            kb = str(k).encode("utf-8") + b"\0"
            if isinstance(v, dict):
                out.append(T_MAP)
                out.extend(kb)
                put(v)
                out.append(T_END)
            elif isinstance(v, bool) or isinstance(v, int):
                out.append(T_INT)
                out.extend(kb)
                out.extend(struct.pack("<I", int(v) & 0xFFFFFFFF))
            elif isinstance(v, str):
                out.append(T_STR)
                out.extend(kb)
                out.extend(v.encode("utf-8") + b"\0")
            else:
                raise TypeError(type(v))

    put(obj)
    out.append(T_END)
    return bytes(out)
