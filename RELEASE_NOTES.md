# STONE OS 0.1.0-rc3

Tercera Release Candidate de STONE OS para STONE MACHINE (AMD BC-250), sobre Bazzite `bazzite-deck` (Fedora 44).

**El software está listo para probar en la placa. Nada está verificado en hardware todavía** (la primera instalación de RC1 en la placa dio observaciones, no pruebas completas de HARDWARE_VALIDATION).

## Cambios desde 0.1.0-rc2

Encontrados con RC2 en la BC-250 (24-10-2026): STONE Home parpadeaba y se veía clara y transparente (se veía Steam detrás), no sonaba y no se abría sola (38 peticiones a Steam en 2 minutos, ninguna atendida).

- **Imagen:** dentro del modo juego STONE Home dibuja por software (GTK `cairo`, WebKit sin composición acelerada, X11). En la placa, los caminos por GPU parpadeaban y salían transparentes, también con `GSK_RENDERER=gl` y con `WEBKIT_DISABLE_DMABUF_RENDERER=1`. La ventana y la página son además siempre opacas. Las opciones de lanzamiento de Steam ya no pueden deshacerlo; `~/.config/stone/home.env` sí (sólo unas pocas variables, valores simples).
- **Sonido:** la política de reproducción automática de WebKit queda en «permitir». Los botones del mando no cuentan como gesto del usuario para WebKit, y con la política estricta el audio quedaba suspendido para siempre. Al arrancar, STONE Home anota en su registro si el audio pudo arrancar.
- **Apertura automática:** el pedido a Steam lleva la pantalla de Steam (`DISPLAY` y demás, tomados del propio proceso de Steam; la sesión los quita a los servicios de usuario). STONE ya no reescribe el identificador que Steam dio al acceso directo, y prueba en orden el identificador con el que Steam abrió STONE la última vez (lo guarda STONE Home al arrancar), el de `shortcuts.vdf` y el calculado. Lo que responde Steam queda en el registro.
- **Diagnóstico en un comando:** `stone-home-session diag` muestra en una pantalla la versión, los identificadores, la apertura de este arranque, el registro de STONE Home (`~/.local/state/stone/home.log`) y lo que anotó Steam.

Sin probar todavía en la placa: si el dibujo por software es lo bastante fluido a 1080p y si el sonido y la apertura quedan resueltos. La primera apertura tras actualizar puede necesitar abrir STONE una vez a mano (así STONE aprende el identificador que usa Steam).

## Cambios desde 0.1.0-rc1

Encontrados al instalar RC1 en la BC-250 (24-10-2026):

- **STONE Home no se abría:** Bazzite 44 arranca el modo juego como `gamescope-session-plus@ogui-steam.service`, y STONE sólo se enganchaba a `@steam`. Ahora el drop-in está en la plantilla `gamescope-session-plus@.service.d/` y vale para cualquier variante; el orden (registro antes de la sesión, apertura después) se declara desde la sesión.
- **Vídeo de arranque de STONE no instalado:** Bazzite deja su propio `deck_startup.webm`, y STONE lo trataba como un vídeo del usuario. Ahora reconoce una copia de un vídeo que trae la imagen (mismo tamaño y SHA-256 que un `.webm` de `/usr/share` o `/etc/skel`), lo reemplaza y lo guarda como `deck_startup.webm.stone-replaced`. Un vídeo propio del usuario sigue sin tocarse.
- **Instalador (GitHub):** la ISO se construye en `ubuntu-24.04` (en 26.04 osbuild no puede montar por AppArmor) y se entrega al usuario del runner antes de partirla.

Observado en la placa con RC1 (no son pruebas completas): instalación con la ISO, arranque con la pantalla STONE (Plymouth), sesión de Steam Game Mode, red, `/usr/lib/stone-release` correcto (kernel 7.2.9, Mesa 26.2.4, regulador con rango de fábrica 1000–1850 MHz).

## IMPLEMENTED

- Imagen bootc derivada de Bazzite con el método de `ublue-os/image-template`, base fijada por digest, metadatos de procedencia en la imagen.
- Arranque STONE (Plymouth), sin reposo (incluida la suspensión de Steam), Handheld Daemon desactivado, comprobación de kernel.
- Regulador de GPU de la BC-250 con su D-Bus activado y su configuración de fábrica comprobada en el build.
- `stoned`: detección de hardware, perfiles SILENT / BALANCED / PERFORMANCE / CUSTOM, aplicación con lectura de vuelta y rollback, prueba de estabilidad (quick 5 min, standard 30 min, extended 2 h), medición de referencia y comparación BEFORE / AFTER, última configuración buena, contador de fallos, Safe Mode (automático, manual, `stonectl`, `stone.safe=1`), vigilancia térmica y de ventilador, telemetría adaptativa, perfiles por juego con detección de Steam.
- **STONE Home en producción:** la interfaz de consola (inicio, biblioteca, ficha de juego, menú rápido, ajustes, Performance Center, STONE TUNING, energía) conectada al `stoned` real; biblioteca y carátulas de Steam; juegos lanzados por Steam; vuelta al inicio al cerrar el juego; apagado y reinicio reales; ajustes guardados; apagado tras inactividad.
- STONE Home dentro de Steam Game Mode: registro automático como acceso directo «STONE» y apertura al arrancar, 2 s después de que aparece la interfaz de Steam y confirmada (se repite hasta que STONE corre), con los tiempos registrados en cada arranque.
- Vídeo de arranque de Steam reemplazado por el de STONE (10 s): continúa la línea de Plymouth, muestra STONE y deja la línea respirando mientras Steam carga; termina en el primer fotograma de STONE Home, que ahora arranca sobre la línea ya dibujada. Nunca sustituye un vídeo propio del usuario.
- GitHub: CI en cada pull request, build de la imagen a GHCR, ISO instalable, release con la ISO en partes y SHA256SUMS.
- Documentación: construir, GitHub paso a paso, instalar, recuperar, arquitectura, dependencias, seguridad, validación en placa.

## SOFTWARE TESTED

Todo lo anterior, con `scripts/test-all.sh` (PASS):

| Suite | Resultado |
| --- | --- |
| `stoned` contra el regulador simulado | 67 / 67 |
| STONE Home (biblioteca, acceso directo, vídeo de arranque, apertura confirmada, host, puente contra `stoned` real) | 20 / 20 |
| Shell real GTK 4 + WebKitGTK 6 contra `stoned` real | PASS |
| De extremo a extremo en Chromium (inicio, PERFORMANCE en el regulador, perfil por juego, lanzar y cerrar juego, apagar) | 7 / 7 pasos |
| ESLint de la página | 0 errores |
| `scripts/validate.sh` (estructura, shellcheck, ruff, XML, TOML, unidades, actionlint, página al día) | PASS |

## READY FOR HARDWARE TEST

Todo el software anterior, pendiente de las 25 pruebas de [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md). En particular, lo que sólo la placa o Steam real pueden confirmar:

- ID PCI de la GPU, interfaz D-Bus real del regulador, sensores y permisos de `stoned`.
- `vkmark --winsys headless` como carga de la prueba.
- Formato de `reaper SteamLaunch AppId=` en la versión de Steam de la imagen.
- STONE Home en Game Mode: registro, apertura automática y su tiempo real, vídeo de arranque (Steam lo reproduce entero y a pantalla completa), foco al volver de un juego, REANUDAR, mando dentro de WebKit, URLs de Steam.
- Recuperación ante cuelgues y cortes de corriente, Safe Mode, vigilancia térmica, ventilador.
- Instalador, primer arranque, actualización y rollback.

## HARDWARE VERIFIED

Ninguno.

## GITHUB ACTIONS

Flujos creados y validados con actionlint (con shellcheck). **WORKFLOW CREATED — NOT EXECUTED ON GITHUB.** La primera ejecución será en el repositorio del propietario.

## KNOWN LIMITATIONS

- **Entre el vídeo y STONE** Steam puede mostrar su inicio unos segundos mientras termina de cargar; el arranque registra cuánto para afinarlo en la placa.
- **Primer inicio de sesión de Steam:** STONE aparece a partir del reinicio siguiente (Steam reescribe su lista de accesos directos al cerrarse; STONE no la toca con Steam abierto).
- **Biblioteca:** sólo juegos instalados con Steam y accesos directos añadidos a Steam. Juegos no instalados, logros y juegos de lanzadores que no se añaden a Steam no aparecen.
- **Durante un juego**, el botón STEAM abre Steam, no STONE. El menú rápido de STONE (☰) está disponible al volver a STONE con un juego abierto; los ajustes rápidos de mando, audio, Bluetooth y red son los de Steam (botón ···). Plugin de STONE para el menú rápido de Steam: siguiente versión.
- **FPS, frametime y 1 % lows** los mide el overlay de rendimiento de Steam (MangoHud); STONE no los duplica.
- **Ventilador:** controlado por la BIOS. STONE sólo lee las RPM y detecta si se para.
- **Mando dentro de STONE Home:** depende de la API Gamepad de WebKitGTK y de la plantilla de Steam Input del acceso directo; prueba 17.
- **Búsqueda en la biblioteca:** no incluida.
- **Firma:** la imagen sólo se firma si se configura `SIGNING_SECRET`; la consola no exige firma al actualizar.
- **Reproducibilidad:** la base está fijada por digest; los paquetes de Fedora y del COPR se resuelven en cada build (versiones registradas, no fijadas).
- **Canales:** RC1 publica `rc` y `dev`; `beta` y `stable` están preparados en el flujo, sin versiones todavía.
- El voltaje no se puede ajustar (por diseño) y el rango de frecuencia es el de fábrica del regulador (en la placa: 1000–1850 MHz).
