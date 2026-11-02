# STONE PERFORMANCE 2.0 (0.1.0-rc11)

Estado: **SOFTWARE TESTED · READY FOR HARDWARE TEST**. Nada de esto está verificado en una BC-250 (ni el máximo de ninguna placa).

## 1. Auditoría

| Pieza | Antes (rc8–rc10) | Ahora |
| --- | --- | --- |
| Techo con 40 CU | `CU40_CEILING_MHZ = 1500` fijo en `backend_bc250.py` | Máximo validado de **esta** consola (`calibration.py`); 1500 solo como línea base sin calibrar |
| Perfiles de fábrica | Números fijos: 1500 / 1850 / 2000 en `profiles.py`, y otra copia en la página (`FACT`) | Calculados del rango de esta máquina (`profiles.factory`). La página toma los valores de `stoned` |
| CUSTOM | Hasta el techo fijo | Hasta el máximo validado |
| Prueba de estabilidad | `vkmark` + log del kernel + temperatura + reloj sostenido | Igual, y en calibración además `stone-cu-verify` (resultados comprobados) y temperatura de CPU |
| Dentro del juego | Perfil fijo; el vigilante térmico baja a SILENT en alerta | Controlador dinámico de techo (calor, objetivo de cuadros cuando exista) por encima del regulador |
| Recuperación | Marcas de prueba y de perfil provisional | También marca de calibración: un cuelgue se atribuye al paso que corría |

**Causa del límite de 1500 MHz:** era una decisión de STONE (clase *STONE CONFIGURATION*), no del hardware ni del regulador.

En rc8 se puso un techo fijo para 40 CU porque:
- el autor del desbloqueo recomienda 1500 MHz / ~900 mV;
- midió 83 °C allí, y 96–107 °C de borde y 220 W de pico sostenidos a 2 GHz con el disipador original;
- no había forma de saber qué aguanta cada placa.

Era seguro, pero igual para todas las consolas. STONE PERFORMANCE 2.0 lo reemplaza por una medición en cada consola.

## 2. Capacidades de la BC-250 (octubre 2026)

| Capacidad | Estado | Fuente / notas |
| --- | --- | --- |
| 40 CU (desbloqueo duggasco, amdgpu en la imagen) | PARTIAL | Funciona en placas probadas por la comunidad; algunas placas tienen CU dañadas. El repositorio original quedó archivado el 17-09-2026; STONE lo compila para cada kernel de Bazzite |
| Control de frecuencia (cyan-skillfish-governor-smu, D-Bus) | SUPPORTED | `SetRange`, `SetTemperatureThresholds`, `SetLoadTarget`, rangos `Allowed/Initial/Current` |
| Voltaje | UNSUPPORTED en STONE (a propósito) | Lo fija la curva de puntos seguros del paquete del regulador; STONE no la toca |
| Temperatura GPU (hwmon `temp1_input`) | SUPPORTED | |
| Temperatura CPU (k10temp) | SUPPORTED | |
| Potencia GPU (hwmon `power1_average`, PPT) | PARTIAL | Lectura del SMU. Sirve para comparar pasos, no como potencia absoluta de la consola |
| Carga GPU (`gpu_metrics` `average_gfx_activity`) | PARTIAL | `gpu_busy_percent` no funciona en la BC-250; el regulador SMU arregla `gpu_metrics`. [VERIFICAR en placa] |
| Ventilador (nct6686 `fan2_input`) | PARTIAL | Solo lectura; depende del cableado del ventilador |
| Cuadros por segundo / tiempo de cuadro del juego | UNKNOWN | Ninguna fuente fiable para `stoned` todavía (MangoHud/Gamescope); el controlador acepta la entrada cuando exista |
| Número de CU activas | SUPPORTED | `amdgpu.bc250_cc_write_mode` en `/sys/module/amdgpu/parameters`, y `active_cu_number` en el kernel |
| Comprobación de CU (resultados correctos) | PARTIAL | `stone-cu-verify` (cómputo comprobado contra la CPU, todas las CU). La prueba por WGP con reinicios de duggasco existe, pero es manual y no sirve en Bazzite atómico |

## 3. Rango de rendimiento (por máquina)

```
hardware   = Range/Allowed del regulador (su curva de voltaje)       p. ej. [1000, 2000]
baseline   = 1500 MHz (min con hardware)                              siempre permitido
validated  = calibración de ESTA consola, en ESTA configuración       si no: 24 CU → hardware.max
                                                                               40 CU → baseline
current    = lo que el regulador informa ahora (leído, nunca supuesto)
```

**Identidad y configuración** (se guardan con el resultado, en `/var/lib/stone/state.json`):
- **Máquina:** hash de la dirección PCI, el dispositivo y la VBIOS. No se guarda nada personal.
- **Configuración:** número de CU, VBIOS, kernel, versión del amdgpu, versión del regulador y versión de la prueba.

Si cualquiera de estos datos cambia, el resultado pasa a **stale**, con el motivo:
- el techo vuelve a la línea base hasta recalibrar;
- se trata igual un cambio de kernel, porque amdgpu y la gestión de energía cambian con él.

**Perfiles redefinidos:**

| Perfil | Techo | Temperatura | Uso |
| --- | --- | --- | --- |
| SILENT | línea base | 75 °C | Ruido y consumo mínimos |
| BALANCED (por defecto) | `min(validado, 1850)` | 85 °C | El valor de fábrica del regulador como tope |
| PERFORMANCE | **validado** | 85 °C | Hasta el máximo validado cuando el juego lo pide |
| CUSTOM | ≤ validado | 65–85 °C | Usuario avanzado |

El regulador sigue moviendo el reloj con la carga. PERFORMANCE no significa «siempre al máximo».

Con 24 CU y sin calibrar, los valores son los de siempre: 1500 / 1850 / 2000.

## 4. STONE CALIBRATION

Iniciada solo por la persona (`stonectl calibrate [quick|standard]`; en el futuro, desde STONE TUNING). Nunca al arrancar ni por un juego. Requiere:
- permiso polkit `performance.tune`;
- que no estén activos el Modo seguro, un juego ni el vigilante térmico;
- la GPU a menos de 70 °C.

Recorrido:

```
LAST KNOWN GOOD guardado
para cada paso de 50 MHz desde la línea base hasta hardware.max:
    marca "calibration" escrita (paso, pasos aprobados)          ← antes de tocar nada
    techo temporal = paso; regulador: [1000, paso], 85 °C; leído de vuelta
    carga: stone-cu-verify (resultados comparados con la CPU) + vkmark (GPU al techo)
    90 s (quick) o 300 s (standard)
    falla si: resultados incorrectos · error amdgpu en el kernel · GPU > 90 °C · CPU ≥ 95 °C ·
              reloj sostenido < 90 % del paso (sin estar caliente) · la carga murió
resultado:
    todo aprobado hasta hardware.max     → validado = hardware.max
    primer fallo en F                    → validado = último aprobado − 50 MHz (margen)
    parada por calor                     → validado = último paso que se mantuvo bajo el límite
    ni la línea base aprueba             → sin validado (con 40 CU: desactivar 40 CU)
    se cuelga o se corta la luz          → al arrancar: el paso en curso cuenta como fallo,
                                           contador de fallos +1, validado = aprobados − margen
techo y perfil último bueno restablecidos; resultado guardado con la configuración
```

STONE nunca pide más que el rango del regulador ni cambia voltajes. Un paso no se usa al arrancar si no pasó la prueba. Cancelar conserva el resultado anterior.

## 5. Controlador dinámico (`controller.py`)

El regulador ya escala el reloj con la carga dentro del techo. El controlador solo mueve el techo durante un juego, dentro de `[línea base, máximo del perfil]`, en pasos de 50 MHz:

- **Calor:**
  - GPU a ≤ 3 °C del límite durante 3 lecturas → un paso abajo;
  - ≥ 10 °C por debajo durante 6 lecturas → un paso arriba.
  - Las protecciones del hardware y del regulador siguen activas; el vigilante térmico sigue bajando a SILENT en alerta.
- **Objetivo de cuadros:**
  - cuando haya tiempo de cuadro (STONE GAME PROFILE) y el juego cumpla el objetivo con holgura a menos del 70 % de carga → un paso abajo, menos consumo y ruido;
  - si no lo cumple a carga alta → un paso arriba.
  - Sin datos de cuadros, esta entrada se ignora.
- Al terminar o caerse el juego, vuelve el perfil global y el ajuste se reinicia.

## 6. API (`org.stonemachine.Performance1`)

| Pedido | Método |
| --- | --- |
| getCapabilities | `GetCapabilities` (+ `range`, `hardware_range`, `calibration`, `cu40.ceiling_mhz` = validado) |
| getActualState | `GetState` (`actual` leído del regulador, `range`, `profiles`, `calibration`, `dynamic`) |
| getPerformanceProfile / setPerformanceProfile | `GetState.profile` / `SetProfile` |
| getCalibrationStatus / getValidatedMaximum | `GetCalibration` (`range.validated_max`, `last`, `running`) |
| startCalibration | `StartCalibration(level)`, `CancelCalibration` |
| getThermals / telemetría | `GetMetrics`: temperaturas, reloj, potencia, carga, `cu_count`, `validated_max_mhz`, `baseline_mhz`, `ceiling_mhz`, `thermal_headroom_c`, `governor`, `profile`, `dynamic_trim_mhz` |
| applyGamePerformanceProfile / restoreGlobalProfile | `SetGameProfile` + detección de juegos de Steam; al terminar o al caerse el juego vuelve el global (ya existía) |

Todos los métodos pasan por el puente de STONE Home (`stone_home/bridge.py`, tipos fijos) y por `stonectl`.

## 7. Archivos

**Nuevos:** `stoned/calibration.py`, `stoned/controller.py`.

**Modificados:**
- en `stoned/`: `service.py`, `profiles.py`, `backend_bc250.py`, `stability.py`, `state.py`, `recover.py`, `ctl.py`;
- `stone_home/bridge.py`;
- en la página: `development/home_parts/{adopt,bridge,catrows}.js` → `index.html`;
- `stone_cu40.py`;
- pruebas: `tests/run_tests.py`, `tests/fake_governor.py`.

## 8. Pruebas (CODE TESTED)

`tests/run_tests.py` P01–P13 cubren:
- **rango y perfiles:** rango y perfiles con 24 y 40 CU; CUSTOM nunca por encima del validado;
- **calibración completa:** llega al máximo del regulador;
- **placa limitada:** una placa que no sostiene más de 1700 MHz valida 1800 (falló a 1900, pasó 1850, menos el margen);
- **cancelar:** conserva el resultado anterior;
- **cuelgue en un paso:** en el arranque cuenta como fallo y conserva solo lo aprobado;
- **resultado stale:** al cambiar el regulador o el kernel;
- **pedidos inválidos y calor al empezar;**
- **controlador:** calor, holgura y falta de cuadros;
- **reglas de calibración;**
- **`stonectl calibrate` y `stonectl calibration`;**
- **calor durante la calibración;**
- **resultados incorrectos de la GPU:** fallan el paso, no se valida nada y la carga acompañante también se detiene.

Pasan todas, junto con las 68 pruebas anteriores de `stoned`.

## 9. Prueba física

En `HARDWARE_VALIDATION.md` §28.


## Placa, 01-11-2026 (rc11)

- **Resultado:** `stonectl calibrate quick` falló en el primer paso, con 40 CU a 1500 MHz: `clock_not_sustained`, máx 48 °C, mediana 58 MHz.
- **`stone-cu-verify` a mano:** 0 errores, con pasadas de GPU de 7 ms.
- **Causa:** la carga de STONE dejaba la GPU sin trabajo casi todo el tiempo.
- **Corrección en rc13:**
  - `stone-cu-verify` mantiene la GPU ocupada y comprueba una muestra;
  - una carga que no ocupa la GPU deja la calibración **no válida** (`load_too_light`), sin contar falla.
- **Pendiente en la placa:**
  - si vkmark headless funciona bajo `stoned`: está en `/var/lib/stone/workload.log`;
  - qué muestra `pp_dpm_sclk` con carga.
