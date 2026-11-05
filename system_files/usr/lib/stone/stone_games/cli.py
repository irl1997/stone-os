"""stone-game: STONE OPTIMIZED from a terminal (Konsole on the console, or SSH as its user).

  stone-game status  <appid> [--json]     profile, availability, environment, recommendation
  stone-game plan    <appid>              the measurement matrix and what is done
  stone-game measure <appid> [--config N] [--delay S] [--duration S]
                                          launch ONE measurement (next in the matrix, or row N)
                                          and wait for the game to end; prints metrics + score
  stone-game play    <appid> [mode]       launch optimized and wait (recommended|quality|performance|custom)
  stone-game rate    <appid> <run> <1-5>  your verdict on the image of a measured run
  stone-game verify  <appid>              HARDWARE VERIFIED (type VERIFICADO): you played it and it is right
  stone-game restore <appid>              STONE RECOMMENDED again (your own choices are removed)
  stone-game export  <appid>              the profile measured here, as JSON (to share)
  stone-game components [helixsr|optiscaler]
                                          what is downloaded; with a name, download the newest official
                                          release from its author (verified, nothing installed in a game)
  stone-game install <appid> optiscaler|helixsr
                                          OptiScaler (FSR 3.1) into the game, or HelixSR's installer command
  stone-game uninstall <appid>            remove what STONE installed in that game
  stone-game recover                      put back any launch options STONE left behind

Runs as the console user, with Steam open (Game Mode): it talks to Steam's interface and to stoned.
"""

import argparse
import json
import os
import sys
import time

from . import optimizer, schema, store


def engine():
    from stone_home import shortcuts, steamcef

    import stone_display

    from .perf import PerfDBus
    return optimizer.Engine(root=shortcuts.steam_root, cef=steamcef,
                            display=lambda: stone_display.collect(game_policy="native"), perf=PerfDBus())


def _appid(v):
    if not v.isdigit():
        raise SystemExit("appid: a Steam AppID (the number in the game's store address)")
    return v


def _wait(e, appid, out):
    print("Esperando a que el juego empiece y termine (Ctrl+C deja de esperar; STONE limpia igual al volver a STONE Home)...")
    started = False
    while True:
        r = e.tick()
        if r == "started" and not started:
            started = True
            print("  el juego empezó")
        elif isinstance(r, dict):
            return r
        if e._session() is None:                        # finished by STONE Home's own monitor
            return {"appid": appid, "reason": "finished by STONE Home"}
        time.sleep(2)


def _print_status(p, rec):
    print(f"{p['name'] or p['appid']} (steam:{p['appid']})   estado: {p['status']}   capas: {', '.join(p['layers']) or '-'}"
          f"   base de datos v{p['db_version']}")
    for err in p["errors"]:
        print("  perfil ignorado:", err)
    f = p["facts"]
    print(f"  instalado: {f['installed']}  build: {f['build']}  Proton: {f['proton'] or 'por defecto'}"
          f"  anti-cheat: {', '.join(f['anti_cheat']) or 'no detectado'}")
    inj = f["injected"]
    print(f"  inyección instalada: {inj['kind'] + ' como ' + str(inj['proxy']) + '.dll' if inj else 'ninguna'}")
    d = p["display"]
    print(f"  pantalla: {'desconocida' if not d['known'] else str(d['output']['w']) + 'x' + str(d['output']['h']) + ' @ ' + str(d['hz']) + ' Hz'}"
          f"  VRR: {d['vrr']['supported']} {d['vrr']['range'] or ''}  objetivos: {p['targets']['targets']}")
    pi = p["performance_input"]
    print(f"  rendimiento: stoned {'sí' if pi['available'] else 'NO'}  perfil {pi['profile']}  máx. validado {pi['validated_mhz']} MHz"
          f"  40 CU: {pi['cu40']}  modo seguro: {pi['safe_mode']}")
    print("  rutas gráficas:")
    for k in schema.PATHS:
        a = p["availability"][k]
        print(f"    {k:14} {'disponible' if a['available'] else 'no'}{'  (' + a['reason'] + ')' if a.get('reason') else ''}")
    if p["stale"]:
        print("  mediciones VENCIDAS por cambio de:", ", ".join(p["stale"]))
    print(f"  STONE RECOMMENDED: {optimizer.summary(rec['config'])}  ({rec['why']})")
    print(f"    en el juego: {rec['settings']}")
    for t in rec["trail"]:
        print("    ·", t)
    print(f"  mediciones: {len(p['measurements'])}   siguiente a medir: "
          f"{optimizer.summary(p['next_measurement']) if p['next_measurement'] else 'ninguna (matriz completa)'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stone-game", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("status", "plan", "measure", "play", "rate", "verify", "restore", "export", "recover",
                                 "components", "install", "uninstall"))
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", type=int)
    ap.add_argument("--delay", type=int, default=90)
    ap.add_argument("--duration", type=int, default=120)
    a = ap.parse_args(argv)
    e = engine()
    if a.cmd == "recover":
        print(json.dumps(e.recover(), indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "components":
        try:
            r = e.fetch_component(a.rest[0]) if a.rest else e.components()
        except schema.ProfileError as err:
            print("error:", err)
            return 1
        print(json.dumps(r, indent=1, ensure_ascii=False))
        return 0
    if not a.rest:
        ap.error("falta el appid")
    appid = _appid(a.rest[0])
    if a.cmd == "status":
        p = e.profile(appid)
        rec = e.recommended(appid, e.chosen_mode(appid), p)
        if a.json:
            print(json.dumps({"profile": p, "recommended": rec}, indent=1, ensure_ascii=False, default=str))
        else:
            _print_status(p, rec)
        return 0
    if a.cmd == "plan":
        p = e.profile(appid)
        done = {}
        for i, m in enumerate(p["measurements"] if not p["stale"] else []):
            done[schema.config_key(dict(m["config"], frame_target={"fps": None, "vrr": False}))] = (i, m)
        rows = optimizer.matrix(p, p["availability"]) + optimizer.stage2(p, p["availability"], p["environment"])
        for n, c in enumerate(rows, 1):
            hit = done.get(schema.config_key(c))
            res = ""
            if hit:
                i, m = hit
                s = m["score"]["recommended"]
                res = (f"  run {i}: {m['metrics']['avg_fps']} FPS, 1% {m['metrics']['low1_fps']}, "
                       f"cv {m['metrics']['frametime_cv']}, GPU {m['metrics'].get('gpu_clock_avg')} MHz "
                       f"{m['metrics'].get('gpu_temp_max')} °C, score {s['score']}{' [' + s['gate'] + ']' if s['gate'] else ''}")
            sus = "  SOSPECHOSA" if optimizer.suspect(p, c) else ""
            print(f"T{n:02d} {'✔' if hit else '·'} {optimizer.summary(c):48}{res}{sus}")
            print(f"      {optimizer.settings_hint(c, p['game'])}")
        unavailable = [k for k in ("helixsr", "optiscaler") if not p["availability"][k]["available"]]
        for k in unavailable:
            print(f"(sin filas {k}: {p['availability'][k]['reason']})")
        return 0
    if a.cmd == "measure":
        p = e.profile(appid)
        cfg = "next"
        if a.config:
            rows = optimizer.matrix(p, p["availability"]) + optimizer.stage2(p, p["availability"], p["environment"])
            if not 1 <= a.config <= len(rows):
                raise SystemExit(f"--config 1..{len(rows)}")
            cfg = rows[a.config - 1]
        r = e.launch(appid, measure={"config": cfg, "delay": a.delay, "duration": a.duration})
        print(json.dumps({k: r.get(k) for k in ("ok", "error", "settings", "graphics_applied", "graphics_error",
                                                "performance", "performance_error", "launch")}, indent=1, ensure_ascii=False))
        if not r.get("ok"):
            return 1
        print(f"MEDICIÓN: {optimizer.summary(r['config'])}\n  {r['settings']}\n"
              f"  MangoHud registra desde el segundo {a.delay} durante {a.duration} s (sin nada en pantalla).")
        res = _wait(e, appid, r)
        print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
        return 0
    if a.cmd == "play":
        mode = a.rest[1] if len(a.rest) > 1 else None
        if mode and mode not in schema.MODES:
            raise SystemExit("modo: " + ", ".join(schema.MODES))
        r = e.launch(appid, mode)
        print(json.dumps({k: r.get(k) for k in ("ok", "error", "why", "settings", "graphics_applied", "performance", "trail")},
                         indent=1, ensure_ascii=False))
        if r.get("ok"):
            print(json.dumps(_wait(e, appid, r), indent=1, ensure_ascii=False, default=str))
        return 0 if r.get("ok") else 1
    if a.cmd == "rate":
        if len(a.rest) != 3 or not a.rest[2].isdigit():
            raise SystemExit("stone-game rate <appid> <run> <1-5>")
        m = e.rate(appid, int(a.rest[1]), int(a.rest[2]))
        print("ok:", m["key"], "imagen", m["image_rating"], "/5")
        return 0
    if a.cmd == "verify":
        p = e.profile(appid)
        rec = e.recommended(appid, "recommended", p)
        print("Vas a marcar como HARDWARE VERIFIED:", optimizer.summary(rec["config"]))
        print("Hazlo sólo si lo jugaste en esta consola y se vio y se sintió bien. Escribe VERIFICADO:")
        if sys.stdin.readline().strip() != "VERIFICADO":
            print("cancelado")
            return 1
        print(json.dumps(e.verify(appid), ensure_ascii=False))
        return 0
    if a.cmd in ("install", "uninstall"):
        kind = a.rest[1] if len(a.rest) > 1 else "optiscaler"
        if kind not in ("optiscaler", "helixsr"):
            raise SystemExit("optiscaler | helixsr")
        try:
            if a.cmd == "uninstall":
                r = e.uninstall_image(appid)
            elif kind == "helixsr":
                r = e.install_plan(appid, "helixsr")
                if r.get("needs_download"):
                    print("Descargando HelixSR del autor...")
                    e.fetch_component("helixsr")
                    r = e.install_plan(appid, "helixsr")
                if r.get("ok"):
                    print(r["warning"], "\nEjecuta:", r["command"])
                    return 0
            else:
                plan = e.install_plan(appid, "optiscaler")
                if plan.get("needs_download"):
                    print("Descargando OptiScaler del autor...")
                    e.fetch_component("optiscaler")
                    plan = e.install_plan(appid, "optiscaler")
                if not plan.get("ok"):
                    r = plan
                else:
                    print(f"Instalaré OptiScaler {plan['component']['tag']} en {plan['folder']} ({plan['exe']}, {plan['files']} archivos). Escribe INSTALAR:")
                    if sys.stdin.readline().strip() != "INSTALAR":
                        print("cancelado")
                        return 1
                    r = e.install_image(appid, "optiscaler")
        except schema.ProfileError as err:
            print("error:", err)
            return 1
        print(json.dumps(r, indent=1, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if a.cmd == "restore":
        print(json.dumps(e.restore_recommended(appid)))
        return 0
    if a.cmd == "export":
        p, _ = store.read_layer("auto", appid)
        print(json.dumps(p or {}, indent=1, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
