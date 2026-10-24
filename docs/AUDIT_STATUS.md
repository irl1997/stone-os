# Estado de la auditoría — STONE OS 0.1.0-rc1

Fuente de verdad: *STONE OS — Hardware Performance & Overclock Audit v1.0* (secciones 12 y 14) y la revisión final de esta versión.

Estados: **FIXED** = corregido y cubierto por pruebas automáticas (CODE TESTED). **READY FOR HARDWARE TEST** = el software está listo y lo que falta sólo se puede comprobar en la placa. Ninguno es HARDWARE VERIFIED.

## Puerta de la Release Candidate

| Criterio | Valor |
| --- | --- |
| P0 de software abiertos | **0** |
| P1 de software abiertos | **0** |
| Simulaciones en producción (`system_files/`) | **0** (comprobado por `scripts/validate.sh`) |
| Pruebas fallidas | **0** (`scripts/test-all.sh`: 67 + 20 + autotest WebKit + 7 pasos end to end + lint) |
| Problemas de seguridad críticos | **0** (SECURITY.md) |
| CI en GitHub | Flujo creado — **NOT EXECUTED ON GITHUB** |
| Build de la imagen | Flujo creado — **NOT EXECUTED ON GITHUB** |
| Hardware | **NOT TESTED** (HARDWARE_VALIDATION.md) |

## Problemas de la auditoría v1.0

| ID | Problema | Estado original | Estado actual | Evidencia | Acción |
| --- | --- | --- | --- | --- | --- |
| P0-1 | Sin LAST KNOWN GOOD | Abierto | FIXED | `state.py`, `service.py` (`StartTest` guarda la última buena antes de la marca y del hardware); pruebas R e I de `run_tests.py` | Placa: pruebas 10–11 |
| P0-2 | Sin protección de arranque | Abierto | FIXED | `recover.py`, `stoned-recover.service` (marcas en disco, contador, apagado limpio en ExecStop); pruebas R | Placa: pruebas 1, 11 |
| P0-3 | Perfil sin validar activo al instante | Abierto | FIXED | Ranura «probada» nunca se aplica al arrancar; validación = STANDARD + partida limpia; pruebas I y G | Placa: prueba 6, 9 |
| P1-1 | Ningún perfil se aplica | Abierto | FIXED | API D-Bus, backend con lectura de vuelta y rollback; puente de STONE Home (`bridge.py`); `test_home.py` B01; end to end (el regulador recibe 2000 MHz) | Placa: prueba 5 |
| P1-2 | Telemetría inventada | Abierto | FIXED | `telemetry.py` sólo sensores reales («—» si faltan); end to end comprueba lecturas del sysfs | Placa: prueba 0 (sensores) |
| P1-3 | Prueba de estabilidad falsa | Abierto | FIXED | `stability.py` (vkmark, journal de amdgpu, umbral térmico, QUICK/STANDARD/EXTENDED) | Placa: pruebas 3, 6 |
| P1-4 | Safe Mode incompleto | Abierto | FIXED | Decidido al arrancar, `stone.safe=1`, `stonectl`, sin perfiles por juego; pruebas S | Placa: prueba 12 |
| P1-5 | Perfil por juego sin identidad | Abierto | FIXED | AppID desde `reaper SteamLaunch`; restauración al desaparecer el proceso; pruebas G; end to end | Placa: pruebas 4, 18 |
| P1-6 | Menú rápido sin estado real | Abierto | FIXED | Menú rápido muestra el estado devuelto por `stoned` | — |
| P1-7 | Sin detección de hardware | Abierto | FIXED | `capabilities()` (PCI 1002:13fe, amdgpu, métodos del regulador, sensores); CUSTOM desactivado si falta algo | Placa: pruebas 0, 24 |
| P1-8 | Sin estados de error | Abierto | FIXED | 12 códigos de error de punta a punta; `test_home.py` B01 (servicio caído → ServiceUnavailable) | — |
| P2-1 | Voltaje falso | Abierto | FIXED | «Curva del regulador»; STONE no toca el voltaje | — |
| P2-2 | Límites fijos en la interfaz | Abierto | FIXED | Rangos desde `GetCapabilities` (Range/Allowed) | — |
| P2-3 | «Respuesta» sin valores | Abierto | FIXED | `profiles.RESPONSE` (cargas objetivo) | — |
| P2-4 | Menú rápido con métricas inventadas | Abierto | FIXED | Lecturas reales o «—» | — |
| P2-5 | RESTORE DEFAULTS no quita perfiles por juego | Abierto | FIXED | `RestoreDefaults`; pruebas S | — |
| P2-6 | Regulador sin versión fijada | Abierto | READY FOR HARDWARE TEST | La versión instalada y su configuración se registran en `stone-release`; `GOVERNOR_VERSION` permite fijarla. Se fijará la que pase las pruebas en placa | Placa: prueba 0; luego fijar en DEPENDENCIES.md |
| P2-7 | Suspensión de Steam no bloqueada | Abierto | FIXED | `sleep.conf.d` + targets enmascarados en `build.sh` | Placa: prueba 14 |
| P2-8 | Sin temperatura de recuperación | Abierto | FIXED | Recuperación = límite − 10 °C | — |
| P2-9 | FPS leídos como ajuste | Abierto | FIXED | «Sugerencia»; STONE no limita FPS | — |
| P3-1 | Watchdog de hardware desconocido | Abierto | READY FOR HARDWARE TEST | Watchdog de systemd para `stoned` implementado; el de la placa sólo se puede comprobar en ella | Placa: prueba 15 |
| P3-2 | Sin BEFORE / AFTER | Abierto | FIXED | `MeasureBaseline`, `stability.compare`; pruebas C | — |
| P3-3 | AUTO TUNE | Por diseño | Por diseño | No se implementa; la API de pruebas y comparación lo permite más adelante | — |
| P3-4 | «PPT» mal rotulado | Abierto | FIXED | «Consumo GPU» | — |
| P3-5 | Control de ventilador por software | Abierto | READY FOR HARDWARE TEST | En producción el ventilador lo controla la BIOS; sólo lectura de RPM y detección de parada | Placa: prueba 13 |

## Revisión final de RC1 (nuevos)

| ID | Severidad | Problema | Estado | Evidencia |
| --- | --- | --- | --- | --- |
| RC-1 | P1 | No existía el puente STONE Home → `stoned`: la interfaz sólo funcionaba con la simulación | FIXED | `stone_home/` (app, host, bridge); `webkit_selftest.py`; `e2e_home.py` |
| RC-2 | P1 | STONE Home, al lanzarse desde Steam, habría sido detectado por `stoned` como un juego y habría tapado los juegos reales (sin perfiles por juego) | FIXED | `games.py` excluye `stone-home`; `test_home.py` K05 |
| RC-3 | P1 | No existía cómo arrancar STONE Home en Game Mode | FIXED (NOT TESTED en Game Mode) | `shortcuts.py`, `session.py`, unidades de usuario; `test_home.py` K02–K04 |
| RC-4 | P1 | La biblioteca era de ejemplo | FIXED | `library.py`; `test_home.py` L01–L03 |
| RC-5 | P2 | Fuentes cargadas desde Google en tiempo de ejecución | FIXED | Fuentes OFL incluidas; CSP sin orígenes externos |
| RC-6 | P2 | Nombres de juegos (archivos del usuario) insertados como HTML | FIXED | `esc()`; end to end con un nombre con etiquetas |
| RC-7 | P2 | Base sin fijar y versión escrita a mano en `stone-release` | FIXED | Digest resuelto y registrado; `stone-release` generado desde `VERSION` |
| RC-8 | P2 | `image-template.env` con un marcador de usuario por rellenar | FIXED | Eliminado; el dueño sale de `GITHUB_REPOSITORY_OWNER` |
| RC-9 | P3 | Archivos de `stoned` legibles por otros por defecto | FIXED | `UMask=0077` en las dos unidades |
| RC-11 | P2 | Arranque: espera fija de 10 s y animación de Steam entre Plymouth y STONE | FIXED (NOT TESTED en placa) | Vídeo de arranque de STONE; apertura a los 2 s con reintentos confirmados; `test_home.py` K04, K06, K07 |
| RC-10 | P2 | La ISO supera el límite de 2 GiB por archivo de GitHub Releases | FIXED | La ISO se publica en partes con SHA-256 de la ISO y de cada parte |

## Inventario de archivos

| Clase | Archivos |
| --- | --- |
| PRODUCTION | `Containerfile`, `VERSION`, `config/build.env`, `build_files/*`, `system_files/**` (stoned, STONE Home, unidades, políticas, Plymouth, fuentes, icono, carátulas), `disk_config/iso.toml` |
| BUILD / RELEASE | `.github/workflows/*.yml`, `.github/actionlint.yaml`, `.github/dependabot.yml`, `scripts/release-info.sh`, `scripts/build-local.sh`, `scripts/build-iso-local.sh` |
| TEST | `tests/run_tests.py`, `tests/fake_governor.py`, `tests/test_home.py`, `tests/steam_fixture.py`, `tests/webkit_selftest.py`, `tests/e2e_home.py`, `tests/e2e/*`, `scripts/test-all.sh`, `scripts/validate.sh` |
| DEVELOPMENT | `development/stone-os-prototipo.html` (prototipo con SimStoned e inyección de fallos), `development/build_home.py`, `development/home_parts/*`, `development/stone_home_devserver.py`, `development/make_assets.mjs` |
| DOCUMENTATION | `*.md` en la raíz, `docs/*.md` |
| OBSOLETE (eliminado) | `image-template.env` (plantilla con marcador), `system_files/usr/lib/stone-release` estático, `docs/HARDWARE-TEST.md` y `docs/RECOVERY.md` (pasaron a la raíz como HARDWARE_VALIDATION.md y RECOVERY.md), `.ruff_cache/` |
| DUPLICATE | Ninguno: la página de producción no se edita a mano, se genera desde el prototipo |

Fuera del repositorio, en la conversación: la guía de montaje HTML y los documentos de diseño (arquitectura, UX, design system, auditorías). No se copian aquí para no tener dos fuentes de la misma información; el repositorio enlaza lo necesario.
