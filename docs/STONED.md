# stoned — STONE System Service

Estado: **CODE TESTED, NOT HARDWARE VERIFIED.** Todo lo de este documento se probó contra un regulador simulado; nada se ha ejecutado en una BC-250.

## Qué hace

`stoned` es la única pieza de STONE que habla con el sistema. La interfaz le pide cambios por D-Bus y muestra lo que `stoned` lee de vuelta del regulador de GPU (ACTUAL STATE > REQUESTED STATE).

- No es root: corre como el usuario de sistema `stoned` (sysusers.d), con aislamiento de systemd.
- No escribe en el hardware: frecuencia, umbral térmico y carga objetivo van por el D-Bus de `cyan-skillfish-governor-smu`. Esos cambios no se guardan en el regulador, así que cualquier reinicio vuelve a su configuración de fábrica (BALANCED).
- No toca voltaje, ventilador ni CPU.

## Archivos

| Ruta en la imagen | Qué es |
| --- | --- |
| `/usr/bin/stoned` | Lanzador (`python3 -I`) |
| `/usr/bin/stonectl` | Estado, Safe Mode y valores de fábrica desde una terminal (ver RECOVERY.md) |
| `/usr/lib/stone/stoned/state.py` | Estado en `/var/lib/stone` (escritura atómica) y marcas de recuperación |
| `/usr/lib/stone/stoned/recover.py` | `stoned --recover` al arrancar y `--mark-clean` al apagar |
| `/usr/lib/stone/stoned/profiles.py` | Perfiles de fábrica y validación de toda entrada |
| `/usr/lib/stone/stoned/backend_bc250.py` | Detección de hardware, cliente del regulador, lecturas de sensores |
| `/usr/lib/stone/stoned/telemetry.py` | Lecturas con frecuencia adaptativa por suscripción |
| `/usr/lib/stone/stoned/thermal.py` | Vigilancia térmica de fondo |
| `/usr/lib/stone/stoned/games.py` | Detección de partidas de Steam (AppID, inicio y fin) |
| `/usr/lib/stone/stoned/stability.py` | Prueba de estabilidad (vkmark) y sus criterios |
| `/usr/lib/stone/stoned/service.py` | API D-Bus `org.stonemachine.Performance1`, polkit |
| `/usr/lib/stone/stability.json` | Comando de la prueba (sólo root lo puede cambiar) |
| `stoned-recover.service`, `stoned.service` | Unidades systemd |
| `org.stonemachine.Performance1.conf` | Política del bus del sistema |
| `org.stonemachine.performance.policy` | Acciones polkit (sesión local activa) |

## API D-Bus

Nombre `org.stonemachine.Performance1`, objeto `/org/stonemachine/Performance1`. Todos los métodos devuelven el estado completo (`a{sv}`), con `actual` leído del regulador.

| Método | Escritura | Qué hace |
| --- | --- | --- |
| `GetCapabilities()` | No | Placa, driver, regulador, rangos (de `Range/Allowed`), sensores |
| `GetState()` | No | Perfil elegido, aplicado, estado real, ranuras, Safe Mode, fallos |
| `GetMetrics()` | No | Una lectura ahora. Un sensor ausente no aparece y figura en `missing` |
| `Subscribe(s)` / `Unsubscribe()` | No | `performance_center` (1 s) u `overlay` (0,5 s). Sin suscriptores no se lee nada. Si el cliente desaparece del bus, su suscripción se borra |
| `HomeReady()` | Sí | Home ya está en pantalla: aplica el perfil guardado si es de fábrica o validado |
| `SetProfile(s)` | Sí | `silent`, `balanced`, `performance`, `c1`–`c3` |
| `StartTest(u, a{sv}, s)` | Sí | Ranura 0–2, valores `max`/`min`/`temp`/`resp`, nivel `quick`/`standard`/`extended` |
| `CancelTest()` | Sí | Cancela, restaura y no guarda nada |
| `MeasureBaseline(s)` | Sí | Medición de referencia (BEFORE) del perfil aplicado, sin cambiar nada; las pruebas del mismo nivel se comparan con ella |
| `SaveTestResult()` / `DiscardTestResult()` | Sí | Una prueba superada sólo deja un resultado pendiente; nada se guarda hasta SAVE |
| `SetSessionProfile(s)` | Sí | Menú rápido: perfil sólo para la partida en curso; al terminar vuelve el global |
| `RestoreDefaults()` | Sí | BALANCED, sin perfiles por juego, regulador a sus valores iniciales; sale de Safe Mode (salvo `stone.safe=1`) |
| `EnterSafeMode()` / `ExitSafeMode()` | Sí | Safe Mode al instante y persistente hasta salir. No se puede salir si lo pidió el arranque (`stone.safe=1`) |
| `SetGameProfile(s, s)` | Sí | AppID de Steam → perfil, o `global` |
| `GameStarted(s)` / `GameEnded(s, b)` | Sí | Sólo con `STONE_GAME_SOURCE=dbus` (pruebas). En la consola devuelven `Unsupported`: las partidas las detecta `stoned` y nadie puede fingir una para validar una ranura |

Errores: `org.stonemachine.Error.` + `Unsupported`, `PermissionDenied`, `InvalidValue`, `HardwareError`, `ServiceUnavailable`, `ThermalLimit`, `ApplyFailed`, `RollbackFailed`, `SafeMode`, `Busy`, `NotValidated`.

Señales: `StateChanged`, `TestFinished`, `Metrics` (al ritmo de la suscripción más rápida) y `ThermalEvent`.

## Lecturas

| Dato | Fuente |
| --- | --- |
| Temperatura GPU | amdgpu hwmon `temp1_input` (edge) |
| Temperatura CPU | k10temp `temp1_input` (Tctl) |
| Frecuencia GPU | `pp_dpm_sclk`, nivel marcado con `*` |
| Uso de GPU | `gpu_metrics` v2, `average_gfx_activity` (0x1C, en 0,01 %); lo corrige el regulador con `fix-metrics`. `0xFFFF` = no disponible [VERIFICAR] |
| Consumo | amdgpu `power1_average`: sólo el chip de GPU |
| Ventilador | NCT6686D `fan2_input` (con `nct6683` o `nct6687`) |
| CPU % y RAM | `/proc/stat`, `/proc/meminfo` |
| VRAM / GTT | `mem_info_vram_used`, `mem_info_gtt_used` |

FPS, frametime y 1 % lows no los lee `stoned`: sólo existen dentro del render del juego y ya los mide MangoHud a través del overlay de rendimiento de Steam.

Coste medido contra sensores simulados: 0,27 ms por lectura completa; 0 % de CPU sin suscriptores, 0,15 % con Performance Center abierto y 0,25 % con el modo técnico; 19 MB de memoria.

## Vigilancia térmica

Siempre activa, una lectura cada 5 s (cada 1 s en alerta). Sólo baja el rendimiento; nunca sube un límite ni desactiva una protección.

| Nivel | Condición | Acción |
| --- | --- | --- |
| Aviso | GPU ≥ 85 °C durante 30 s | `ThermalEvent` |
| Reducir | GPU ≥ 88 °C durante 30 s, o el ventilador deja de girar con la GPU ≥ 70 °C | SILENT aplicado temporalmente; se rechaza subir el techo |
| Restaurar | Reducir con una ranura CUSTOM en uso | Vuelve a BALANCED y la ranura deja de estar validada |
| Recuperar | GPU ≤ 75 °C durante 60 s y ventilador girando | Vuelve el perfil en uso |
| Crítico | GPU ≥ 92 °C con SILENT aplicado | Aviso crítico. `stoned` no apaga la consola: eso lo hace el corte térmico de la placa |

Umbrales tomados de la documentación comunitaria (85 °C recomendado, 90 °C máximo). [AJUSTAR en placa]

## BEFORE / AFTER

Cada prueba de una ranura que pasa se compara con la última medición de referencia del mismo nivel. Se compara la frecuencia sostenida (mediana), la temperatura máxima, el consumo medio de GPU y el porcentaje de tiempo limitado por temperatura (a menos de 1 °C del límite y por debajo del 95 % del techo). No se comparan FPS: sólo existen dentro del juego.

| Veredicto | Cuándo |
| --- | --- |
| `better` | La frecuencia sostenida sube un 2 % o más |
| `no_gain` | Sube menos de un 2 %: más techo no mejora en esta consola |
| `worse` | Baja, o pasa 10 puntos o más de tiempo extra limitada por temperatura (overclock negativo) |
| `insufficient` | No hay medición de referencia |

## Configuración del regulador en la imagen

`build_files/governor-config.py` toma el archivo de configuración del paquete del regulador y cambia **sólo** `[dbus] enabled = true`. La curva de voltaje, el rango y el umbral térmico quedan como los publica el paquete. El build falla si el paquete no trae D-Bus, si su rango sale de 1000–2000 MHz o si no tiene umbral térmico ni curva. La versión instalada y los valores quedan en `/usr/lib/stone-release`. Para fijar una versión probada en placa: `GOVERNOR_VERSION=<versión>` en el build.

La imagen también enmascara `sleep.target`, `suspend.target`, `hibernate.target`, `hybrid-sleep.target` y `suspend-then-hibernate.target`, para que la suspensión por inactividad de Steam Game Mode no pueda dejar la consola colgada.

## Partidas

`stoned` detecta las partidas por el proceso que Steam usa para lanzarlas: `reaper SteamLaunch AppId=<id> -- …` (gamescope identifica los juegos de la misma forma). Sólo lee `/proc/<pid>/cmdline`, que cualquier usuario puede leer; no necesita acceso a la carpeta del usuario ni a los archivos de Steam. [VERIFICAR en placa: `ps -eo args | grep SteamLaunch`]

- **Inicio:** aparece un `reaper` con `AppId`. Se aplica el perfil de ese juego, si tiene uno.
- **Fin:** el proceso desaparece, sea porque el juego se cerró, se colgó o Steam se cayó. Vuelve el perfil global.
- **Partida limpia** (la única que puede validar una ranura CUSTOM): duró al menos 10 minutos y el registro del kernel no muestra errores de amdgpu desde que empezó. Si el registro no se puede leer, no valida.
- **Coste:** un recorrido de `/proc` cada 2 s, leyendo sólo los procesos nuevos: 0,03 % de CPU en reposo con 400 procesos.
- **STONE Home no es un juego:** también se lanza desde Steam (acceso directo «STONE»), pero `stoned` ignora el `reaper` cuyo programa es `stone-home`. Así nunca recibe un perfil por juego ni tapa al juego real que se abre desde él.
- **No se detectan** los juegos lanzados fuera de Steam (por ejemplo Heroic abierto por su cuenta): usan el perfil global. Los accesos directos que no son de Steam pero se lanzan desde Steam sí se detectan, con su ID de acceso directo.
- Si `stoned` se reinicia con un juego abierto, lo vuelve a detectar.

## Ciclo de una ranura CUSTOM

1. **Probar:** `StartTest` guarda la última configuración buena, escribe la marca de prueba y aplica los valores en caliente.
2. **Terminar:** vuelve a la última buena. Si pasó, queda un resultado pendiente.
3. **Guardar:** `SaveTestResult` guarda la ranura como `tested`. Nunca la activa. `DiscardTestResult` la descarta y la ranura anterior queda intacta.
4. **Usar:** `SetProfile("c1")` sobre una ranura `tested` la aplica sólo hasta apagar (marca `provisional`).
5. **Validar:** STANDARD superada + una partida limpia (10 min sin errores de GPU) → `validated`. Sólo entonces puede aplicarse al encender.

## Arranque

`stoned --recover` decide sin tocar el hardware:

- Apagado limpio → contador a 0.
- Sin apagado limpio con prueba, ranura provisional, CUSTOM activa o un arranque que no llegó a Home → contador + 1, ranura provisional marcada `failed`, perfil a la última buena.
- El contador sólo vuelve a 0 con un apagado limpio o con RESTORE DEFAULTS: dos pruebas que cuelgan la consola seguidas llevan a Safe Mode aunque Home llegue a aparecer entre ellas.
- Contador ≥ 2, `stone.safe=1` en la línea de arranque, o Safe Mode activado a mano (desde la interfaz o con `stonectl`) → Safe Mode. El estado indica el motivo: `failures`, `kernel` o `manual` (nada por encima de BALANCED, sin perfiles por juego, sin pruebas).

## La interfaz

El prototipo se suscribe a las lecturas sólo mientras Performance Center, STONE TUNING o el modo técnico están en pantalla. Un sensor ausente se muestra como «—».

El prototipo habla con `stoned` sólo a través de la STONE Performance API (`PerfAPI`), con los mismos métodos, errores y estado. En el prototipo, `PerfAPI` es `SimStoned`, una simulación de `stoned` y del regulador; el panel de desarrollo permite inyectar fallos (servicio caído, regulador que ignora cambios, GPU caliente, permiso denegado, sin regulador, errores de GPU en la prueba, sin sensor de ventilador, ventilador detenido). En el prototipo los tiempos de la vigilancia térmica están acortados ×0,1 para poder verla. En STONE OS sólo cambia el transporte: un puente de STONE Home a D-Bus. [PENDIENTE: depende de la tecnología final de STONE Home]

## Pruebas en la placa

Ver `docs/HARDWARE-TEST.md`: cada comando con qué hace, cómo revertirlo, su riesgo y el resultado esperado, y la matriz de resultados (todo NOT TESTED hasta hacerlo en una BC-250).

## Pruebas

```
python3 tests/run_tests.py
```

Necesita `dbus-daemon` y `python3-gobject`. Cada prueba levanta un bus D-Bus privado, un regulador falso (`tests/fake_governor.py`), un árbol sysfs falso y un `journalctl` falso.

## Pendiente de verificar en placa

- Que el regulador acepta llamadas del usuario `stoned`, y los nombres reales de sus propiedades (`min`/`max` o `Min`/`Max`).
- Si `SetRange` exige activar antes el modo rendimiento del regulador.
- El ID PCI de la GPU (`1002:13fe`) con `lspci -nn`.
- `vkmark --winsys headless` como carga de la prueba, y su acceso al nodo de render.
- Que el usuario `stoned` puede leer el journal del kernel (grupo `systemd-journal`).
