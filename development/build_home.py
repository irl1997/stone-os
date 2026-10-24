#!/usr/bin/env python3
"""Derive the production STONE Home page from the development prototype.

    development/stone-os-prototipo.html  ->  system_files/usr/share/stone-home/index.html

The prototype stays the design reference and QA tool (simulated BC-250, fault injection,
debug bar). This script keeps its interface code - layout, THE LINE, motion, sound,
focus model, gamepad, Performance Center - and replaces only what was simulated:

  * SimStoned and every sim.* call      -> the real stoned API through STONE Home's host
  * generated example library and art  -> Steam's installed games and cached artwork
  * debug bar, fault injection, crash  -> removed
  * Google Fonts                        -> bundled OFL fonts (no network at runtime)
  * power, launch, settings links       -> logind, steam://, real system information

Every replacement checks that the text it replaces is present exactly once, so a change
in the prototype makes this script fail instead of producing a half-converted page.
Run:  python3 development/build_home.py   (CI checks the committed page is up to date)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(HERE, "stone-os-prototipo.html")
OUT = os.path.join(REPO, "system_files/usr/share/stone-home/index.html")
PARTS = os.path.join(HERE, "home_parts")


def part(name):
    with open(os.path.join(PARTS, name), encoding="utf-8") as f:
        return f.read().rstrip("\n")


def between(text, start, end, inclusive_end=False):
    a = text.index(start)
    b = text.index(end, a)
    return a, (b + len(end) if inclusive_end else b)


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        sys.exit(f"build_home: '{label}' found {n} times in the prototype (expected 1)")
    return text.replace(old, new)


def replace_span(text, start, end, new, label, inclusive_end=False):
    if text.count(start) != 1:
        sys.exit(f"build_home: start of '{label}' found {text.count(start)} times")
    a, b = between(text, start, end, inclusive_end)
    return text[:a] + new + text[b:]


def build(src):
    t = src
    # ---- document head: our own, no wrapper, no remote fonts --------------------------
    t = replace_span(t, "<!doctype html>", "/* Layout:", part("head.html") + "\n", "head")
    t = replace_once(t, "/* Layout: a fixed 1920×1080 console stage, scaled to fit the window, with a slim legend bar beneath. Single dark look by design. */",
                     "/* Layout: a fixed 1920×1080 console stage, scaled to fit the screen. Single dark look by design. */", "layout comment")
    t = replace_span(t, "#app{height:100%", "#viewport{", "#app{height:100%;box-sizing:border-box;display:flex;flex-direction:column}\n", "app css")
    t = replace_span(t, "#bar{display:flex", "\n/* Theme modifiers", "", "bar css")
    # The game screen sits behind the game: a still image, no animation spending GPU.
    t = replace_once(t, "#game .art{position:absolute;inset:-2%;background-size:cover;background-position:center;animation:drift 24s ease-in-out infinite alternate}\n#game:not(.on) .art{animation:none}\n@keyframes drift{from{transform:scale(1.02) translateX(0)}to{transform:scale(1.06) translateX(-1.5%)}}",
                     "#game .art{position:absolute;inset:0;background-size:cover;background-position:center;opacity:.35}", "game art css")
    t = replace_once(t, "@media (prefers-reduced-motion: reduce){#game .art{animation:none}}\n#stage.rm #game .art{animation:none}\n", "", "drift media")
    t = replace_once(t, '<div id="offmsg">STONE MACHINE · Pulsa A o Enter para encender</div>', '<div id="offmsg"></div>', "offmsg")
    t = replace_span(t, '  <div id="bar">', "</div>\n\n<script>", "</div>\n\n<script>", "bar markup", inclusive_end=True)

    # ---- script ------------------------------------------------------------------------
    t = replace_span(t, "/* ---------- State ---------- */", "/* ---------- STONE PERFORMANCE API ----------", part("state.js") + "\n", "state")
    t = replace_span(t, "/* ---------- STONE PERFORMANCE API ----------", "class PerfError", part("api_comment.js") + "\n", "api comment")
    t = replace_once(t, "const TEST_MS = { quick: 12000, standard: 20000 };   /* 5 and 30 min on STONE MACHINE */\n", "", "TEST_MS")
    t = replace_span(t, "const SimStoned = (() => {", "const noLimitText", part("bridge.js") + "\n\n", "SimStoned")
    t = replace_span(t, "function adopt(st) {", "/* ---------- Readings:", part("adopt.js") + "\n", "adopt/api")
    t = replace_once(t, "${m.simulated ? ' · lecturas simuladas' : ''}", "", "readings simulated")
    t = replace_once(t, " + (m.simulated ? '<div class=\"kv\"><span>Datos</span><span>simulados</span></div>' : '')", "", "tuning simulated")
    t = replace_span(t, "/* ---------- Example library ---------- */", "/* ---------- Sound ----------", part("library.js") + "\n\n", "library")
    t = replace_span(t, "function bgGame(g, sharp, veil) {", "const neutralBg", part("bggame.js") + "\n", "bgGame")
    t = replace_span(t, "/* ---------- HOME ---------- */", "/* ---------- LIBRARY ---------- */", part("home.js") + "\n\n", "home")
    t = replace_span(t, "const FILTERS = [", "function enterLibrary(", part("libfilter.js") + "\n", "filters")
    t = replace_once(t, "<div class=\"count\">${list.length} juegos · Recientes</div>", "<div class=\"count\">${list.length} juegos · recientes primero</div>", "count")
    t = replace_once(t, "${list.length ? '' : '<div class=\"empty\">No hay juegos con este filtro.</div>'}", "${list.length ? '' : `<div class=\"empty\">${emptyText()}</div>`}", "empty")
    t = replace_once(t, "if (el.dataset.g !== undefined) { const g = G[+el.dataset.g]; s.textContent = `${g.n} · ${g.inst ? (g.h ? g.last + ' · ' + g.h + ' h' : 'Sin jugar') : 'No instalado · ' + g.sz} · ${g.o}`; }",
                     "if (el.dataset.g !== undefined) { const g = G[+el.dataset.g]; s.textContent = `${g.n} · ${g.upd ? 'Actualizando en Steam' : g.min ? g.last + ' · ' + playText(g) : 'Sin jugar'} · ${g.o}`; }", "lib onFocus")
    t = replace_span(t, "function libCard(g, row, col) {", "function libraryA(el) {", part("libcard.js") + "\n", "libCard")
    t = replace_span(t, "/* ---------- DETAIL ---------- */", "/* ---------- QUICK MENU ---------- */", part("detail_game.js") + "\n\n", "detail+game")
    t = replace_span(t, "function openQuick() {", "/* Quick Menu: this game session only", part("quick.js") + "\n", "quick")
    t = replace_span(t, "function catRows() {", "function enterSettings(", part("catrows.js") + "\n", "catRows")
    t = replace_once(t, "  if (master) master.gain.setTargetAtTime(S.sound ? S.vol : 0, AC.currentTime, .05);\n  $('#bSound').textContent = `Sonido: ${S.sound ? 'activado' : 'desactivado'}`;\n",
                     "  if (master) master.gain.setTargetAtTime(S.sound ? S.vol : 0, AC.currentTime, .05);\n", "applyPrefs bar")
    t = replace_once(t, "  applyPrefs(); play('nav'); enterSettings({ pane: true, ri });\n}", "  applyPrefs(); savePrefs(); armIdleOff(); play('nav'); enterSettings({ pane: true, ri });\n}", "adjust save")
    t = replace_span(t, "  if (r.act === 'steam') { play('select'); return toast('Se abre el panel de Steam con el tema de STONE (fuera del prototipo)', false); }\n  if (r.act === 'desk') { play('error'); return toast('El modo escritorio queda oculto en el producto final', false); }\n",
                     "  if (r.act === 'perf')", "  if (r.act === 'steam' || r.act === 'info') { play('select'); return toast(r.where, false); }\n", "settingsA steam")
    t = replace_once(t, " · ${real} · regulador simulado en el prototipo`;", " · ${real}`;", "statusLine")
    t = replace_once(t, "  const m = sim(S.prof, .35), rpm = 1300 + Math.round((m.temp - 55) * 35), mr = capRange('max_range', [1500, 2000]), nr = capRange('min_range', [1000, 1500]), tr = capRange('temp_range', [65, 85]);",
                     "  const mr = capRange('max_range', [1500, 2000]), nr = capRange('min_range', [1000, 1500]), tr = capRange('temp_range', [65, 85]);", "tuningRows sim")
    t = replace_once(t, "{ l: 'FPS, resolución, refresco, VRR y escalado', s: 'Los gestiona Steam con el tema de STONE', v: 'Abre el panel de Steam', act: 'steam' },",
                     "{ l: 'FPS, resolución, refresco, VRR y escalado', s: 'Los gestiona Steam durante el juego', v: 'En Steam', act: 'steam', where: 'Durante el juego: botón ··· del mando → Rendimiento' },", "tuning steam row")
    t = replace_once(t, "      <div class=\"pbox\"><h3>ESTIMACIÓN EN JUEGO</h3>${(m => `<div class=\"kv\"><span>Frecuencia sostenida</span><b>${m.clk} MHz</b></div><div class=\"kv\"><span>Temperatura</span><b>${m.temp} °C</b></div><div class=\"kv\"><span>Consumo GPU</span><b>${m.ppt} W</b></div>`)(draftSim())}<div class=\"kv\"><span>Datos</span><span>simulados</span></div></div>\n", "", "estimate box")
    t = replace_once(t, "function draftSim() { return sim({ n: 'draft', ...D }); }\n", "", "draftSim")
    t = replace_once(t, "    onA: el => { closeDlg(); if (el.dataset.b === 'yes') { S.advAck = true; S.adv = true; play('select'); } else play('back'); enterTuning({ ri }); },",
                     "    onA: el => { closeDlg(); if (el.dataset.b === 'yes') { S.advAck = true; S.adv = true; savePrefs(); play('select'); } else play('back'); enterTuning({ ri }); },", "adv ack save")
    t = replace_once(t, "  if (r.act === 'steam') { play('select'); return toast('Se abre el panel de rendimiento de Steam (fuera del prototipo)', false); }",
                     "  if (r.act === 'steam') { play('select'); return toast(r.where, false); }", "tuningA steam")
    t = replace_once(t, "<p>La prueba rápida carga la GPU 5 minutos y la estándar 30 en STONE MACHINE; aquí se simulan en 12 y 20 segundos. Para validar",
                     "<p>La prueba rápida carga la GPU 5 minutos y la estándar 30. Para validar", "review text")
    t = replace_span(t, "let testUI = null, lastTests = [];", "function testPassed(ri, r) {", part("tests.js") + "\n", "test UI")
    t = replace_once(t, "frecuencia sostenida ${r.median_clock_mhz} MHz (simulado).", "frecuencia sostenida ${r.median_clock_mhz} MHz.", "passed simulated")
    t = replace_span(t, "/* Fail-safe demo:", "function safeModeDlg() {", part("bootnotices.js") + "\n", "crash+notices")
    t = replace_span(t, "/* ---------- POWER ---------- */", "/* ---------- Input dispatch ---------- */", part("power_boot.js") + "\n\n", "power+boot")
    t = replace_span(t, "function act(a) {", "  const dirs = ['up', 'down', 'left', 'right'];", part("act_head.js") + "\n", "act head")
    t = replace_once(t, "    if (a === 'y') return toast('Búsqueda: fuera de este prototipo', false);\n", "", "search")
    t = replace_once(t, "    if (a === 'b') { play('back'); clearInterval(installT); installT = 0; line.classList.remove('load'); return S.from === 'library'",
                     "    if (a === 'b') { play('back'); return S.from === 'library'", "detail back")
    t = replace_once(t, "function touched() {\n  if (S.deep)", "function touched() {\n  armIdleOff();\n  if (S.deep)", "touched idle")
    t = replace_once(t, "  if (S.screen === 'off' || S.screen === 'asleep') return act('a');\n", "", "click off")
    t = replace_span(t, "/* Bar buttons */", "})();\n</script>", part("start.js") + "\n})();\n</script>", "start", inclusive_end=True)
    for bad in ("SimStoned", "PerfAPI.sim", "Math.random() * 8", "simulad", "prototipo", "fonts.googleapis", "bCrash", "bFault"):
        if bad in t:
            line = t[:t.index(bad)].count("\n") + 1
            sys.exit(f"build_home: '{bad}' still present at line {line}")
    return t


def main():
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    out = build(src)
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8") as f:
            if f.read() != out:
                sys.exit("build_home: index.html is out of date; run python3 development/build_home.py")
        print("build_home: index.html is up to date")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"build_home: wrote {os.path.relpath(OUT, REPO)} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
