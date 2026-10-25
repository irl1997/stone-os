# STONE Home

La interfaz de consola de STONE OS. Estado: **CODE TESTED** (shell real GTK/WebKit contra `stoned` real bajo Xvfb, página completa en Chromium de extremo a extremo). Dentro de Steam Game Mode en la BC-250: **NOT TESTED** (HARDWARE_VALIDATION.md, pruebas 16–20 y 23).

## Cómo arranca

```
Plymouth (línea blanca) ─► vídeo de arranque de Steam = vídeo de STONE (continúa la línea) ─► Steam carga ─► STONE Home
gamescope-session-plus@<variante>.service (usuario; en Bazzite 44: @ogui-steam)
  ├─ Wants (drop-in de la plantilla: gamescope-session-plus@.service.d/10-stone-home.conf)
  ├─ stone-home-register.service   Before Steam: acceso directo «STONE» y vídeo de arranque
  └─ stone-home-autostart.service  After Steam: en cuanto steamwebhelper lleva 2 s vivo, pide
         steam://rungameid/<STONE> y repite cada 3 s hasta que el proceso de STONE Home existe
         └─ Steam lanza /usr/bin/stone-home (bajo reaper, con Steam Input)
```

- **Vídeo de arranque:** `/usr/share/stone-home/boot/stone-startup.webm` se copia a `~/.steam/root/config/uioverrides/movies/deck_startup.webm`, el archivo que Game Mode reproduce en lugar de su propia animación. Empieza en el mismo fotograma en que está Plymouth (negro, línea al 82 % de la altura y 25 % del ancho), muestra STONE (0–4 s) y después deja sólo la línea respirando como en Plymouth (4–10 s) mientras Steam termina de cargar. El último fotograma es el primero de STONE Home (negro y la línea completa), así que no hay salto ni si STONE Home aparece durante la cola del vídeo ni si aparece después. 1920×1080, 60 fps, 10 s, VP9, 57 KB, sin sonido (el sonido de inicio lo pone STONE Home). Nunca reemplaza un vídeo propio del usuario: sólo actualiza el archivo que STONE instaló (marca `deck_startup.webm.stone` con su SHA-256) o una copia de un vídeo que trae la propia imagen (Bazzite deja el suyo ahí; se reconoce por tamaño y SHA-256 contra los `.webm` de `/usr/share` y `/etc/skel`) y guarda ese como `deck_startup.webm.stone-replaced`. Se genera con `development/make_boot_video.mjs`.
- **Apertura rápida y confirmada:** antes había una espera fija de 10 s. Ahora la primera petición sale 2 s después de que aparece la interfaz de Steam (`STONE_HOME_SETTLE_S`) y, si Steam todavía la ignora porque está cargando, se repite cada 3 s (`STONE_HOME_RETRY_S`) hasta que el proceso de STONE Home existe; entonces no se envía nada más. Cada arranque deja una línea con los tiempos medidos (`journalctl --user -u stone-home-autostart`): `steam_ui_after=… requests=… home_after=…`. Con esos datos se ajustan los dos valores en la unidad (HARDWARE_VALIDATION.md, prueba 16).
- **Sesión:** la primera prueba en la placa mostró que Bazzite 44 arranca `gamescope-session-plus@ogui-steam.service`, no `@steam`. El drop-in va en la plantilla (`gamescope-session-plus@.service.d/`), así vale para cualquier variante, y el orden (registro antes, apertura después) se declara desde la sesión.
- **Cómo se abre:** en el modo juego `steam://rungameid/<id>` llega a Steam (`ExecuteSteamURL` en su registro) pero no abre accesos directos que no son de Steam (placa, RC3). La apertura usa la interfaz de Steam: `stone_home/steamcef.py` lee las páginas en `http://127.0.0.1:8080/json` y ejecuta `SteamClient.Apps.RunGame` en `SharedJSContext` con un cliente WebSocket mínimo de la biblioteca estándar. Requiere `<Steam>/.cef-enable-remote-debugging`, que crea el registro antes de que arranque Steam. La URL queda de respaldo. Seguridad: SECURITY.md.
- **Identificador real:** Steam puede dar su propio identificador a un acceso directo que no es de Steam (ValveSoftware/steam-for-linux#9463). STONE ya no lo reescribe; al arrancar, STONE Home guarda en `~/.local/state/stone/steam-ids.json` los identificadores con los que Steam lo abrió (`SteamGameId`, `AppId=` del reaper), y la apertura automática los prueba primero. El pedido lleva el `DISPLAY` del proceso de Steam (la sesión lo quita a los servicios de usuario). `stone-home-session diag` lo muestra todo en una pantalla.
- **Registro:** sólo con Steam cerrado (Steam reescribe `shortcuts.vdf` al salir). Nunca escribe sobre un archivo que no puede leer; guarda `shortcuts.vdf.stone-backup` la primera vez; conserva los accesos directos del usuario; renumera como espera Steam. Instala la carátula de STONE en `config/grid/`.
- **Identificador:** `appid = crc32("\"/usr/bin/stone-home\"" + "STONE") | 0x80000000`, escrito explícitamente en el acceso directo; el ID de juego para `steam://rungameid` es `(appid << 32) | 0x02000000`.
- **Si algo falla**, la unidad termina sin error y Steam muestra su propia pantalla: la consola sigue usable. `stone-home-session status` dice qué pasó.
- **Lo que queda visible:** si Steam espera a que termine el vídeo antes de mostrar su interfaz, los 10 s cubren la carga y STONE Home aparece sobre la misma línea. Si Steam lo corta al estar listo, o lanza STONE recién al terminar, puede verse su inicio un momento. Las dos cosas sólo se saben en la placa (prueba 16); la duración se cambia en `development/make_boot_video.mjs` (`SECONDS`).

## Proceso y página

| | |
| --- | --- |
| Proceso | `/usr/bin/stone-home` quita del entorno las bibliotecas del runtime de Steam y su overlay (`LD_LIBRARY_PATH`, `LD_PRELOAD`); dentro del modo juego fija el dibujo por software (`GDK_BACKEND=x11`, `GSK_RENDERER=cairo`, `WEBKIT_DISABLE_COMPOSITING_MODE=1`, `WEBKIT_DISABLE_DMABUF_RENDERER=1`; en la placa los caminos por GPU parpadeaban y salían transparentes), aplica `~/.config/stone/home.env` (sólo esas variables, `ENABLE_GAMESCOPE_WSI` y `STONE_HOME_DEVTOOLS`, valores simples), escribe todo en `~/.local/state/stone/home.log` y ejecuta `/usr/libexec/stone-home` (`python3 -I`); usuario de la consola, nunca root |
| Ventana | `Gtk.ApplicationWindow` a pantalla completa con un `WebKit.WebView` |
| Página | `stone://home/index.html`; fuentes en `stone://home/fonts/` |
| Carátulas | `stone://art/<appid>/<capsule|hero|logo|header>`, resueltas por `library.art_path()` |
| Peticiones | `window.webkit.messageHandlers.stone.postMessage({method, args})` → `Promise` con `{ok, result}` o `{ok:false, code, message}` |
| Eventos | `window.__stoneEvent(name, data)`: `state`, `metrics`, `testFinished`, `thermal`, `service` |
| Navegación | sólo `stone://home/`; sin ventanas nuevas, sin menú contextual, sin red |
| Seguridad del contenido | CSP: sólo recursos propios y `stone:`; nombres de juegos insertados como texto |
| Proceso web caído | se recarga la página; el estado que importa vive en `stoned` |

## Métodos (host.py)

| Método | Qué hace |
| --- | --- |
| `perf.<método>` | Los 17 métodos de `stoned` permitidos (bridge.py): estado, capacidades, métricas, perfiles, pruebas, Safe Mode, perfiles por juego, suscripción de telemetría. Tipos comprobados antes de D-Bus; `stoned` vuelve a validar todo |
| `library` | Escanea la biblioteca de Steam en un hilo |
| `launch(appid)` | Sólo un juego de la última lista; abre su `steam://rungameid/…` |
| `openSteam(store|downloads|screenshots)` | URL fija de Steam |
| `power(off|reboot)` | logind `PowerOff` / `Reboot` |
| `systemInfo` | `/usr/lib/stone-release`, hora de arranque, despliegues de `rpm-ostree status --json` |
| `prefs.get`, `prefs.set` | `~/.config/stone/home.json`, con esquema fijo y valores comprobados |
| `selftest` | Sólo con `--selftest` |

No hay ningún método que ejecute un comando, abra una ruta o una URL elegida por la página.

## Biblioteca

| Dato | Origen |
| --- | --- |
| Carpetas de biblioteca | `steamapps/libraryfolders.vdf` |
| Juegos instalados, nombre, tamaño, estado | `<biblioteca>/steamapps/appmanifest_<id>.acf` |
| Tiempo de juego, última partida | `userdata/<cuenta>/config/localconfig.vdf` (la cuenta usada más recientemente) |
| Accesos directos de otros lanzadores y emuladores | `userdata/<cuenta>/config/shortcuts.vdf` |
| Carátulas | `config/grid/` del usuario, luego `appcache/librarycache` (formato antiguo y carpetas por AppID) |

Se excluyen Proton, Steam Linux Runtime y redistribuibles. Un archivo dañado se omite, nunca bloquea la interfaz. Los juegos de lanzadores que no se añaden a Steam no aparecen (limitación de RC1).

## Del prototipo a producción

`development/build_home.py` toma `development/stone-os-prototipo.html` (la referencia de diseño, con simulación) y sustituye sólo lo simulado, con las piezas de `development/home_parts/`:

| Prototipo | Producción |
| --- | --- |
| `SimStoned`, `sim.*`, inyección de fallos, cuelgue simulado | `PerfAPI` → host → `stoned` |
| Biblioteca y arte generados | Biblioteca y carátulas de Steam |
| Barra de depuración | Eliminada |
| Google Fonts | Fuentes OFL incluidas |
| Encender / suspender simulados | Aparición de STONE Home; apagado y reinicio por logind; sin suspensión (la BC-250 no tiene reposo) |
| Pantalla de juego animada | Imagen fija tenue detrás del juego |
| Ajustes «fuera del prototipo» | Indicación exacta de dónde está cada ajuste en Steam |

Diseño, navegación, THE LINE, movimiento, sonido, mando y Performance Center no cambian. El script falla si alguna parte que reemplaza ya no existe en el prototipo, y `scripts/validate.sh` falla si la página no está regenerada.

## Desarrollo

```
STONE_BUS=session python3 development/stone_home_devserver.py --steam-root <carpeta de Steam> \
    --fake-steam /tmp/launches.jsonl --fake-power /tmp/power.jsonl
```

Sirve la misma página por HTTP en `127.0.0.1` con el mismo host. Con un `stoned` en el bus de sesión y el regulador simulado (como hace `tests/e2e_home.py`), toda la consola funciona en un navegador.
