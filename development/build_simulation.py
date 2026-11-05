#!/usr/bin/env python3
"""A self-contained page that plays STONE OS with simulated data (for looking at the interface, not for measuring).

It is the production page (system_files/usr/share/stone-home/index.html) with the transport to the host
replaced by development/sim_host.js and the stoned simulation of the prototype. Nothing in it is measured.

    python3 development/build_simulation.py OUT.html
"""
import base64
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PAGE = os.path.join(REPO, "system_files/usr/share/stone-home")


def rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main(out):
    page, proto = rd(os.path.join(PAGE, "index.html")), rd(os.path.join(HERE, "stone-os-prototipo.html"))
    a = proto.index("const SimStoned = (() => {")
    b = proto.index("const PerfAPI = SimStoned;", a)
    sim_stoned = proto[a:b]
    f0 = proto.index("function sim(id, load) {")
    f1 = proto.index("/* ---------- STONE PERFORMANCE API", f0)
    sim_stoned = proto[f0:f1] + sim_stoned          # the prototype's reading model, used by SimStoned's metrics
    start = page.index("const Bridge = (() => {")
    end = page.index("/* Everything else the console needs", start)
    page = page[:start] + sim_stoned + "\n" + rd(os.path.join(HERE, "sim_host.js")) + "\n" + page[end:]
    page = re.sub(r"url\((fonts/[^)]+\.woff2)\)", lambda m: "url(data:font/woff2;base64,"
                  + base64.b64encode(open(os.path.join(PAGE, m.group(1)), "rb").read()).decode() + ")", page)
    page = re.sub(r"<meta http-equiv=\"Content-Security-Policy\"[^>]*>\n?", "", page)
    page = re.sub(r"<title>.*?</title>", "", page)
    page = page.replace("<!doctype html>", "").replace("</head><body>", "")
    page = re.sub(r"<html[^>]*><head>", "", page)
    page = re.sub(r"<meta (charset|name)=[^>]*>\n?", "", page)
    page = page.replace("</body></html>", "")
    page = '<title>STONE OS Simulación</title>\n' + page + "\n" + rd(os.path.join(HERE, "sim_panel.html"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"{out}: {len(page)} bytes")


if __name__ == "__main__":
    main(sys.argv[1])
