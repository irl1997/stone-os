# Arquitectura de STONE OS 0.1.0-rc2

```
STONE OS UI (STONE Home)          GTK 4 + WebKitGTK 6, usuario de la consola, dentro de Steam Game Mode
        ↓  mensajes WebKit (tabla fija de métodos)
STONE Home host                    stone_home/host.py · bridge.py · library.py · system.py
        ↓  D-Bus de sistema (org.stonemachine.Performance1, polkit)
STONE System Service (stoned)      usuario `stoned`, sin privilegios, systemd con aislamiento
        ↓  BC-250 Performance Backend (backend_bc250.py)
Linux / Bazzite                    D-Bus del regulador, sysfs/hwmon de sólo lectura, /proc
        ↓
AMDGPU · cyan-skillfish-governor-smu · Gamescope · Steam
        ↓
AMD BC-250
```

Principio: **ACTUAL STATE > REQUESTED STATE.** La interfaz muestra lo que `stoned` lee de vuelta del regulador, nunca lo que pidió.

## Cadena completa, del encendido al apagado

| # | Etapa | Componente | Archivos | Estado |
| --- | --- | --- | --- | --- |
| 1 | POWER ON | BIOS P3.00, UEFI | docs/FIRMWARE.md | Documentado; NOT TESTED |
| 2 | BOOT | GRUB, kernel de Bazzite, initramfs con el tema STONE | `build_files/build.sh` (dracut), `system_files/usr/share/plymouth/themes/stone/` | Implementado; NOT TESTED |
| 3 | Kernel / AMDGPU | Kernel de Bazzite; el build falla con rangos de kernel con fallos conocidos en la BC-250 | `build_files/check-kernel.sh` | Implementado; NOT TESTED |
| 4 | Regulador de GPU | `cyan-skillfish-governor-smu` (COPR), arranca desde su archivo de fábrica con su D-Bus activado | `build.sh`, `build_files/governor-config.py` | Implementado; NOT TESTED |
| 5 | Recuperación | `stoned-recover.service`: decide (nunca aplica): contador de fallos, última buena, Safe Mode, `stone.safe=1` | `stoned/recover.py`, `state.py` | CODE TESTED |
| 6 | Servicio | `stoned.service` (Type=notify, watchdog, reinicio, aislamiento): API D-Bus, backend BC-250, telemetría, vigilancia térmica, partidas de Steam | `stoned/*.py`, unidades, D-Bus, polkit | CODE TESTED |
| 7 | Sesión | SDDM inicia sesión automática en Steam Game Mode (`gamescope-session-plus@ogui-steam` en Bazzite 44; STONE se engancha a cualquier variante de `gamescope-session-plus@`) | Bazzite | Sin cambios |
| 8 | STONE Home: registro | `stone-home-register.service` (usuario, antes de Steam): añade el acceso directo «STONE» a Steam e instala el vídeo de arranque de STONE como vídeo de Steam (continúa la línea de Plymouth) | `stone_home/shortcuts.py`, `session.py` | CODE TESTED; NOT TESTED en Game Mode |
| 9 | STONE Home: apertura | `stone-home-autostart.service`: 2 s después de que aparece la interfaz de Steam pide `steam://rungameid/<STONE>` y repite cada 3 s hasta que STONE Home corre | `shortcuts.py`, `session.py` | CODE TESTED; NOT TESTED en Game Mode |
| 10 | STONE Home | Ventana GTK/WebKit; `HomeReady` → sólo entonces `stoned` aplica el perfil guardado (fábrica o validado) | `stone_home/app.py`, `index.html` | CODE TESTED (shell real + navegador) |
| 11 | Juego | JUGAR → `steam steam://rungameid/<appid>`; Steam lo lanza bajo `reaper`; `stoned` lo detecta por `/proc` y aplica el perfil del juego | `host.py`, `system.py`, `stoned/games.py` | CODE TESTED |
| 12 | Fin del juego | El `reaper` desaparece (salida, cuelgue o caída de Steam) → `stoned` vuelve al perfil global → STONE Home vuelve al inicio | `games.py`, `service.py`, `index.html` | CODE TESTED |
| 13 | Apagado | Animación → logind `PowerOff`/`Reboot` → systemd detiene `stoned` y escribe la marca de apagado limpio | `system.py`, `stoned-recover.service` (ExecStop) | CODE TESTED |

No queda ninguna capa crítica sólo conceptual. Lo que depende del comportamiento real de Steam, Gamescope o la placa está marcado NOT TESTED y tiene su prueba en HARDWARE_VALIDATION.md.

## Decisiones

**1. Imagen derivada de Bazzite con el método de `ublue-os/image-template`.** Verificado en el repositorio de la plantilla en octubre de 2026: `Containerfile` + `build.sh` + `bootc container lint`, publicación en GHCR, ISO con `osbuild/bootc-image-builder-action` (`anaconda-iso`). Se descartan BlueBuild (capa extra) y una distribución propia. Bazzite sigue dando kernel, Mesa, Steam, Gamescope y actualizaciones.

**2. Base `bazzite-deck`, fijada por digest.** Es la variante que arranca en Steam Game Mode. Bazzite publica `stable`, `stable-44` y `stable-44.AAAAMMDD` (verificado en su flujo de build); STONE usa `stable-44` resuelto a digest en cada build y registrado en la imagen.

**3. STONE Home dentro de Steam Game Mode, como acceso directo de Steam.** Decisión 2 del documento de arquitectura (fase 1): Steam sigue siendo el motor de la sesión y la red de seguridad (entrada, overlay, actualizaciones del sistema); STONE Home se lanza como una aplicación más. Es el mismo modelo que usan los frontends de emulación en Game Mode. Si STONE Home falla, Steam sigue funcionando.

**4. STONE Home = GTK 4 + WebKitGTK 6 (Python), no Electron ni Tauri.** WebKitGTK 6 y GTK 4 están en los repositorios de Fedora (sin runtime propio que mantener), PyGObject ya lo usa `stoned`, y la interfaz es la misma página del prototipo (HTML/CSS/JS). La página no tiene acceso a red, archivos ni procesos: sólo a un esquema propio `stone://` y a una tabla fija de métodos.

**5. IPC: D-Bus de sistema entre STONE Home y `stoned`.** Es el mecanismo estándar de Fedora para servicios de sistema (polkit decide por llamada, systemd activa y supervisa), y el regulador ya expone D-Bus. Dentro de STONE Home, la página habla con su proceso por el manejador de mensajes de WebKit (sin sockets ni puertos).

**6. Partidas detectadas por `stoned`, no por la interfaz.** `stoned` lee `/proc/<pid>/cmdline` buscando `reaper SteamLaunch AppId=<id>`. Así los perfiles por juego funcionan y se retiran aunque STONE Home se caiga. STONE Home mismo se excluye (también corre bajo `reaper`).

**7. Actualizaciones: bootc/rpm-ostree de Bazzite, sin actualizador propio.** La consola sigue el canal con el que se instaló; Steam (Ajustes → Sistema) y las actualizaciones automáticas de Bazzite usan ese origen. La versión anterior queda para rollback.

## STONE Home

Detalle en [docs/STONE_HOME.md](docs/STONE_HOME.md).

| Pieza | Qué hace |
| --- | --- |
| `app.py` | Ventana a pantalla completa, esquema `stone://` (archivos de la interfaz y carátulas), manejador de mensajes con respuesta asíncrona, autotest `--selftest` |
| `host.py` | Tabla única de métodos para cualquier transporte (WebKit en la consola, HTTP en desarrollo) |
| `bridge.py` | Cliente D-Bus de `stoned`: métodos permitidos con tipos fijos, códigos de error, señales sólo del dueño del nombre |
| `library.py` | Biblioteca desde los archivos de Steam (juegos instalados, accesos directos, tiempo de juego, carátulas) |
| `system.py` | Apagado/reinicio (logind), URLs de Steam permitidas, versión y despliegues, ajustes de la interfaz |
| `shortcuts.py`, `session.py` | Acceso directo «STONE» en Steam y apertura al arrancar |
| `index.html` | La interfaz: generada desde el prototipo por `development/build_home.py` |

## Producción y desarrollo

| | Producción (`system_files/`, entra en la imagen) | Desarrollo (`development/`, `tests/`, nunca en la imagen) |
| --- | --- | --- |
| Rendimiento | `stoned` real contra el regulador real | `tests/fake_governor.py`, SimStoned del prototipo |
| Telemetría | sensores reales; un sensor que falta es «—» | sysfs simulado en las pruebas |
| Biblioteca | archivos de Steam | `tests/steam_fixture.py`, biblioteca de ejemplo del prototipo |
| Interfaz | `index.html` generado, sin barra de depuración | `development/stone-os-prototipo.html` con inyección de fallos y cuelgue simulado |
| Transporte | WebKit | `development/stone_home_devserver.py` (HTTP, sólo 127.0.0.1) |

`scripts/validate.sh` comprueba que no hay código de simulación en `system_files/` y que ninguna unidad systemd activa un interruptor de pruebas (`STONE_POLKIT`, `STONE_BUS`, `STONE_GAME_SOURCE`, …). Esos interruptores existen en el código sólo para las pruebas y sólo los puede fijar quien controla el entorno del servicio (root).

## Estructura del repositorio

```
stone-os/
├── .github/
│   ├── workflows/ci.yml                 validación + todas las pruebas (PR, push, llamado por los demás)
│   ├── workflows/build-image.yml        imagen bootc → GHCR
│   ├── workflows/build-installer.yml    ISO (bootc-image-builder, anaconda-iso)
│   ├── workflows/release.yml            etiqueta v<VERSION> → CI → imagen → ISO → GitHub Release
│   ├── actionlint.yaml, dependabot.yml
├── Containerfile                        FROM bazzite-deck@digest → build.sh → bootc container lint
├── VERSION                              origen único de la versión
├── config/build.env                     nombre de imagen, base, constructor de ISO (sin secretos)
├── build_files/                         build.sh, check-kernel.sh, governor-config.py
├── system_files/                        todo lo que entra en la imagen, con su ruta final
│   ├── usr/bin/                         stoned, stonectl, stone-home, stone-home-session
│   ├── usr/libexec/stone-home           proceso de STONE Home (lanzado con entorno limpio)
│   ├── usr/lib/stone/stoned/            STONE System Service
│   ├── usr/lib/stone/stone_home/        STONE Home (shell, host, puente, biblioteca, sistema, Steam)
│   ├── usr/lib/stone/stability.json     carga de la prueba de estabilidad
│   ├── usr/lib/systemd/system/          stoned-recover.service, stoned.service
│   ├── usr/lib/systemd/user/            stone-home-register/-autostart + drop-in de Game Mode
│   ├── usr/lib/systemd/sleep.conf.d/    sin reposo
│   ├── usr/lib/sysusers.d, tmpfiles.d   usuario `stoned`, /var/lib/stone
│   ├── usr/share/dbus-1/system.d/       política del nombre D-Bus
│   ├── usr/share/polkit-1/actions/      acciones polkit
│   ├── usr/share/plymouth/themes/stone/ arranque
│   └── usr/share/stone-home/            index.html, fuentes OFL, icono, carátulas de Steam
├── disk_config/iso.toml                 instalador (Anaconda) y canal de actualización
├── scripts/                             validate.sh, test-all.sh, release-info.sh, build-local.sh, build-iso-local.sh
├── tests/                               pruebas (stoned, STONE Home, WebKit, end to end), dobles de prueba
├── development/                         prototipo, generador de la página, servidor de desarrollo, recursos
└── docs/                                STONED.md, STONE_HOME.md, AUDIT_STATUS.md, FIRMWARE.md
```
