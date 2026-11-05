# STONE OPTIMIZED — Game Profile Engine (0.1.0-rc12)

Estado: **SOFTWARE TESTED · READY FOR HARDWARE TEST**.

- Ningún perfil está medido en una BC-250. El de Cyberpunk 2077 es un punto de partida sin números; este documento no incluye benchmarks.
- No existe un estado «STONE VERIFIED». **HARDWARE_VERIFIED** sólo lo pone una persona en la consola, después de jugar y sobre una configuración medida en esa misma consola.

**Principio:** BEST EXPERIENCE, NOT BIGGEST NUMBER. La persona elige el juego y pulsa JUGAR. STONE decide con lo que sabe de verdad: la pantalla, el rendimiento validado de su consola, lo que hay instalado y lo que se midió.

## 1. Arquitectura: STONE GAME PROFILE

Todo vive en el paquete `system_files/usr/lib/stone/stone_games/`, que corre dentro del proceso de STONE Home (el usuario de la consola):

| Módulo | Qué hace |
| --- | --- |
| `schema.py` | Define el perfil: campos, validación, versión (`PROFILE_VERSION = 1`) y estados. |
| `store.py` | Base de datos en tres capas, versionada (§5). |
| `detect.py` | Lee del disco, sin tocar nada: carpeta del juego, `buildid` (versión), Proton, archivos anti-cheat, HelixSR/OptiScaler instalados (DLL proxy y huella de los archivos). |
| `graphics.py` | STONE GRAPHICS (§2). |
| `metrics.py` | Métricas reales desde el CSV de MangoHud. |
| `score.py` | STONE EXPERIENCE SCORE (§4). |
| `optimizer.py` | STONE OPTIMIZER (§3): objetivos, matriz, sesiones, recomendación, caídas, invalidación. |
| `perf.py` | Conexión con `stoned`, que es el único que toca la GPU. |
| `cli.py` | La orden `stone-game`. |

**Un perfil** tiene esta forma (el identificador es `steam:<AppID>`; el prefijo deja sitio a Heroic, GOG o Epic):

```text
schema, id, appid, name, status, revision
game          api, native_upscalers, anti_cheat, online_only, benchmark, notes
modes         recommended | quality | performance | custom  →  CONFIG
candidates    matriz de referencia (perfiles incorporados)
invalidation  entorno en que se midió
measurements  métricas + puntuación por modo, salida e interna, valoración de imagen
failures      g:<gráficos> / p:<perfil>, contados por separado
safety        next_safe / next_safe_perf (próximo lanzamiento seguro)
verified      qué confirmó la persona y cuándo (sólo HARDWARE_VERIFIED)
```

**CONFIG** contiene:

- `graphics`:
  - `path`: native, game_upscaler, helixsr, optiscaler o gamescope;
  - `upscaler_preset`;
  - `internal`: estrategia y escala;
  - `frame_generation`: siempre `false` en las configuraciones automáticas;
  - `game_settings`: el texto con lo que la persona ajusta en el menú del juego.
- `performance.profile`: silent, balanced o performance.
- `frame_target`: fps y VRR.
- `gamescope.scaling_filter`.

**Entradas:**

- **STONE DISPLAY:** salida, nativa, Hz, VRR (rango), HDR y modos. Si la pantalla es desconocida, se asume 60 Hz sin VRR. Nunca se da 4K por hecho.
- **STONE PERFORMANCE:** perfil global, Safe Mode, máximo validado, 40 CU, regulador y perfiles por juego que eligió la persona.

**Estados del perfil:**

| Estado | Significado |
| --- | --- |
| UNKNOWN | Sin datos. Incluye los perfiles incorporados sin medir. |
| AUTO_GENERATED | Medido en esta consola. |
| RECOMMENDED | Reservado para perfiles de la base de STONE construidos con mediciones reales. Hoy no hay ninguno. |
| USER_MODIFIED | La persona eligió CUSTOM. |
| HARDWARE_VERIFIED | La persona confirmó con `stone-game verify`. Cualquier medición nueva lo devuelve a AUTO_GENERATED. |

## 2. STONE GRAPHICS: estado del motor

STONE controla cada juego solamente a través de sus **opciones de lanzamiento de Steam**:

1. Las lee (`RegisterForAppDetails`) y guarda las originales en `~/.local/state/stone/game-graphics.json` **antes** de cambiar nada.
2. Las escribe (`SetAppLaunchOptions`) y las vuelve a leer para comprobar que quedaron.
3. Al salir del juego pone las originales. Si la persona las cambió mientras jugaba, se quedan las suyas.

Las variables de STONE van delante de las opciones de la persona. Si la persona ya definió una de esas variables, gana la suya. Nunca se apilan dos `WINEDLLOVERRIDES`.

| Ruta | Estado | Cómo |
| --- | --- | --- |
| **NATIVE** | Implementada | Sin inyección. El juego renderiza a la resolución de salida. |
| **GAME NATIVE FSR** (también XeSS) | Implementada; el ajuste dentro del juego lo hace la persona | Sin inyección. STONE indica qué elegir en el menú del juego, porque no edita archivos de configuración de los juegos. |
| **HELIXSR** | Implementada si la persona lo instaló como `winmm.dll` (o `version.dll`, etc.) | `WINEDLLOVERRIDES="winmm=n,b"` solamente en ese lanzamiento. Sin esa opción, Wine no carga la DLL: apagar la ruta no deja ningún residuo. |
| HelixSR instalado como `dxgi.dll` | Detectada, **no conmutable** | Con ese nombre se carga siempre. STONE lo informa: NATIVE y FSR del juego aparecen como no disponibles (mezclados), con la indicación de reinstalarlo como `winmm.dll`. No mueve ni borra archivos de la persona. |
| **OPTISCALER** (sin HelixSR) | Igual que HelixSR | Mismo mecanismo. |
| **GAMESCOPE SCALING** | Sólo orientación | En Game Mode, Steam controla Gamescope (··· → Rendimiento → Filtro de escalado). |
| Límite de cuadros | Implementado, [VERIFICAR en placa] | `DXVK_FRAME_RATE` / `VKD3D_FRAME_RATE`. |
| Generación de cuadros | Nunca automática | Sólo CUSTOM. Las mediciones nunca la usan. |

**HelixSR 3.1.0:**

- Está hecho sobre OptiScaler (GPL-3) y se desarrolló en una BC-250.
- STONE no lo incluye. Desde rc20, a petición de la persona, lo descarga del repositorio oficial de su autor (`lonewolf0622/HelixSR`); el instalador oficial (`helixsr-setup.sh`) se ejecuta a mano porque instala en todos los juegos que encuentra con `--yes`.
- Es el **candidato preferido** solamente cuando, medido en esta consola, iguala a la mejor experiencia (§4). Nunca se fuerza.

**Anti-cheat y juegos online:** HelixSR y OptiScaler no se ofrecen a un juego si se cumple cualquiera de estas condiciones:

- tiene archivos anti-cheat (EasyAntiCheat, BattlEye, Vanguard, mhyprot, XIGNCODE, nProtect, EA Anti-Cheat);
- la base de datos lo marca con anti-cheat;
- es solo online.

Esto vale también cuando la persona los pidió en CUSTOM. En esos casos quedan las rutas propias del juego.

**FALLBACK (gráficos):**

- HELIXSR → FSR DEL JUEGO → NATIVE.
- OPTISCALER → FSR DEL JUEGO → NATIVE.

Cada paso es un lanzamiento limpio, porque la DLL inyectada sólo se carga con la opción de STONE y esa opción se quita antes del siguiente lanzamiento.

## 3. STONE OPTIMIZER: arquitectura

**Al empezar un juego:**

1. Se elige el juego.
2. Se detecta la pantalla.
3. Se carga el perfil.
4. Se valida contra:
   - el entorno (invalidación);
   - lo que está disponible ahora;
   - las caídas anteriores.
5. Se aplican los gráficos: opciones de lanzamiento, comprobadas.
6. Se aplica el rendimiento: perfil por juego en `stoned`. Sólo si la configuración se midió o es CUSTOM. Si la persona eligió un perfil por juego en STONE Performance, se respeta.
7. Se verifica.
8. Steam lanza el juego.
9. Se vigila: el proceso `reaper SteamLaunch AppId=` se consulta cada 3 s, y solo mientras hay una sesión.

**Al salir del juego:**

1. Se guardan las métricas (en lanzamientos de medición).
2. Se restaura el rendimiento global: se quita el perfil por juego que puso STONE, y `stoned` vuelve además al global por su cuenta.
3. Se limpian las opciones de lanzamiento.
4. Se vuelve a STONE Home.

**Si el juego se cae** (sale antes de 60 s, o STONE Home encuentra al arrancar una sesión que nunca terminó):

1. Se guarda el estado.
2. Se restaura el rendimiento.
3. Se limpian los gráficos.
4. Se cuenta el fallo:
   - **gráficos**, si había inyección;
   - **rendimiento**, si STONE puso un perfil por encima de BALANCED.
5. El siguiente lanzamiento es seguro:
   - gráficos: el siguiente paso del fallback;
   - rendimiento: BALANCED.
6. Una partida normal devuelve la elección habitual.
7. Con 2 caídas, la configuración queda **sospechosa**: deja de recomendarse hasta que cambie el entorno.

Gráficos y rendimiento se recuperan por separado. La recuperación propia de `stoned` (Last Known Good → BALANCED) sigue a cargo de las caídas de GPU.

**Optimización medida** (sin machine learning):

- **Etapa 1:**
  - la matriz incorporada del juego, si la hay;
  - después, cada ruta disponible con los presets Calidad, Equilibrado y Rendimiento;
  - todo en BALANCED.
- **Etapa 2:** las dos mejores de la etapa 1, en SILENT y en PERFORMANCE. Es el ciclo rendimiento + gráficos: medir, cambiar el rango de la GPU, volver a medir. Nunca se pasa del máximo validado, porque `stoned` lo rechaza.
- Se mide un lanzamiento cada vez. Cada medición se puntúa y los modos del perfil se recalculan.
- El siguiente lanzamiento normal es CARGAR → VERIFICAR → JUGAR, sin repetir la prueba.

**Objetivos de experiencia** (`score.targets`, sin una regla fija de 60):

| Pantalla | PERFORMANCE | RECOMMENDED | QUALITY |
| --- | --- | --- | --- |
| 60 Hz sin VRR | 60 | 60 | 30 |
| 120 Hz sin VRR | 60 | 60 | 40 |
| 120 Hz con VRR 48–120 | 90 | 60 | 48 |
| 144 Hz sin VRR | 72 | 48 | 36 |
| Desconocida | 60 | 60 | 30 |

Sin VRR sólo valen cadencias que dividen la frecuencia de la pantalla (sin judder). Con VRR vale cualquier valor dentro del rango.

El límite de cuadros que se aplica es el más alto que esa configuración **sostuvo** al medirla: con VRR, media ≥ 1,05× y 1 % low dentro del rango; sin VRR, 1 % low ≥ 0,92×.

**INVALIDACIÓN:** las mediciones caducan y vuelve a ofrecerse la matriz si cambia:

- `game_build`;
- `proton`;
- `injected` (la huella de los archivos de HelixSR/OptiScaler);
- `gpu_backend` (24/40 CU, regulador);
- `calibration` (máximo validado);
- `display` (nativa, Hz, VRR);
- `engine` (las reglas de STONE).

Mientras tanto se usa la base incorporada o la configuración segura. Si sólo cambia la versión de STONE OS, se vuelve a comprobar la disponibilidad y se conservan las mediciones.

## 4. STONE EXPERIENCE SCORE (fórmula inicial)

```text
SCORE = A·wA + S·wS + Q·wQ + H·wH + E·wE      (cada parte 0..1; los pesos suman 100)

A  objetivo      0,6·min(1, media/T) + 0,4·min(1, 1%low/(0,75·T))
S  estabilidad   (1 − 4·máx(0, cv − 0,05)) · (1 − 0,5·máx(0, p99/p50 − 1,3)) · (1 − tirones_por_min/30)
Q  imagen        PRIOR: reconstrucción(ruta) · (0,55 + 0,45·escala_interna)
                 native 1,0 · FSR/XeSS del juego, HelixSR, OptiScaler 0,80 · Gamescope 0,60
                 Si la persona valoró la imagen (stone-game rate, 1..5): Q = nota/5
H  térmico       mín(1 − (GPU_máx − 75)/15, 1 − (CPU_máx − 85)/10); desconocido = 0,5
E  eficiencia    reloj GPU medio más bajo de las mediciones del juego / el de esta; desconocido = 0,5
```

| Modo | A | S | Q | H | E |
| --- | --- | --- | --- | --- | --- |
| RECOMMENDED | 35 | 30 | 20 | 10 | 5 |
| QUALITY | 25 | 25 | 35 | 10 | 5 |
| PERFORMANCE | 40 | 35 | 10 | 10 | 5 |

**Topes:**

- Salida anormal: 0.
- Media < 0,5·T o 1 % low < 0,4·T: máximo 40.
- GPU ≥ 90 °C o CPU ≥ 95 °C: máximo 30.
- Pocos cuadros: no hay puntuación.

**Desempate:**

1. **Eficiencia:** dentro de 2 puntos de experiencia (la puntuación sin E), gana el reloj más bajo. Así, 40 CU a 1600 MHz gana a 40 CU a 2000 MHz si se juega igual.
2. **HelixSR:** a igualdad, es el preferido.

**Métricas** (`metrics.py`):

- Se descartan los primeros 5 s.
- `avg_fps` = cuadros / tiempo.
- 1 % low = 1000 / media del 1 % de cuadros más lentos.
- Frametime: media, desviación, varianza, p50 y p99.
- cv = desviación / media.
- Tirón = cuadro de más de 2,5× la mediana.
- Reloj GPU, temperaturas GPU y CPU, potencia. La potencia de la BC-250 está **marcada como no validada** (`power_reliable: false`).
- La resolución de salida es la real. La interna es la **esperada** por el preset (`expected: true`): el juego no la informa.

## 5. Base de datos de perfiles

| Capa | Ruta | Quién la escribe |
| --- | --- | --- |
| BUILT-IN | `/usr/share/stone/game-profiles/steam/<appid>.json` + `index.json` (`db_version`) | La imagen. Se comprueba en CI y al construir. |
| AUTO GENERATED LOCAL | `~/.local/share/stone/game-profiles/auto/<appid>.json` | Las mediciones de esta consola. |
| USER | `~/.local/share/stone/game-profiles/user/<appid>.json` | La persona (CUSTOM). |

- Al leer, las capas se combinan campo a campo: usuario > auto > incorporada.
- Un archivo dañado se ignora y se informa.
- La futura **STONE PROFILE DATABASE** llegará con las actualizaciones de la imagen en este mismo formato (o como descarga firmada a la misma ruta). No hay servidor.
- `stone-game export <appid>` saca el perfil medido para compartirlo.

## 6. API

STONE Home expone estos métodos (`host.py`, desde la página con `Sys.game*` en `bridge.js`):

| Pedido | Método del host | Devuelve |
| --- | --- | --- |
| getGameProfile(appId) | `game.profile` | Estado, capas, base, disponibilidad de cada ruta y el motivo, mediciones caducadas, objetivos, siguiente medición |
| getRecommendedExperience(appId, modo?) | `game.recommended` | Etiqueta, resumen, motivo, ajuste en el juego, estado, objetivo, salida e interna esperada, camino de decisiones |
| getAvailableModes(appId) | `game.modes` | Modo elegido y los cuatro modos |
| applyGameProfile(appId, modo, config?) | `game.apply` | CUSTOM guarda la configuración (USER_MODIFIED) |
| launchOptimized(appId, modo?) | `game.launchOptimized` | Igual que JUGAR |
| getOptimizationStatus() | `game.status` | Sesión activa: juego, configuración, medición, gráficos y rendimiento aplicados |
| restoreRecommended(appId) | `game.restoreRecommended` | Quita las elecciones de la persona; las mediciones se quedan |

- **JUGAR** (`launch`) usa STONE OPTIMIZED en los juegos de Steam. La preferencia `optimized` (1 por defecto) lo desactiva.
- Si cualquier paso falla, el juego se lanza como lo dejó la persona.

**Interfaz mínima:**

- La ficha del juego muestra `STONE RECOMMENDED · <resumen> · <estado>`.
- Al pulsar JUGAR, si hay un perfil medido, un aviso dice qué ajustar en el menú del juego.
- No hay rediseño.

**Terminal** (`stone-game`): status, plan, measure, play, rate, verify, restore, export y recover.

## 7. Cyberpunk 2077: primer perfil de referencia

Perfil incorporado `steam/1091500.json`:

- DX12; DLSS, FSR y XeSS propios; sin anti-cheat; no es solo online.
- Estado UNKNOWN.
- RECOMMENDED de partida: FSR del juego en Calidad, GPU Equilibrado, sin límite. **No es un resultado.**

**CYBERPUNK TEST MATRIX.** Común a todas las filas: preset ALTO, trazado de rayos DESACTIVADO, generación de cuadros DESACTIVADA y salida a la nativa del televisor.

| Fila | Ruta | Interna esperada (4K) | GPU |
| --- | --- | --- | --- |
| T01 | Nativo (TAA) | 3840×2160 | BALANCED |
| T02–T04 | FSR del juego Calidad / Equilibrado / Rendimiento | 2561×1441 / 2258×1270 / 1920×1080 | BALANCED |
| T05–T07 | HelixSR (DLSS en el menú) Calidad / Equilibrado / Rendimiento | ídem | BALANCED (sólo si HelixSR está instalado como `winmm.dll`) |
| T08 | OptiScaler (DLSS → FSR 3.1) Calidad | 2561×1441 | BALANCED (sólo si OptiScaler está instalado sin HelixSR) |
| Etapa 2 | Las 2 mejores × SILENT y PERFORMANCE | | ≤ máximo validado |

Nada peligroso: ni generación de cuadros, ni perfiles por encima del máximo validado, ni inyección con anti-cheat.

## 8. Procedimiento de validación física

Está en `HARDWARE_VALIDATION.md` §29 y en la guía rc12.

**Resumen:**

1. Instalar rc12 y usar 40 CU calibrado.
2. TV 4K con salida 3840×2160.
3. HelixSR instalado como `winmm.dll`.
4. `stone-game status 1091500`.
5. `stone-game plan 1091500`.
6. Para cada fila: `stone-game measure 1091500`, cargar la partida en el punto fijo y recorrer la ruta.
7. `stone-game rate` para valorar la imagen.
8. `stone-game plan`.
9. Jugar la recomendada 30 min.
10. `stone-game verify`.
11. `stone-game export` → el PRIMER PERFIL REAL DE STONE.

## 9. Pruebas pendientes de hardware

- Que `SetAppLaunchOptions` / `RegisterForAppDetails` funcionen en la versión de Steam de la imagen, y que la opción se aplique al lanzar en Game Mode.
- Que MangoHud deje el CSV con `no_display`, `autostart_log` y `log_duration` dentro de Gamescope, por cuadro (`log_interval=0`), con reloj y temperaturas de la BC-250.
- Que `WINEDLLOVERRIDES=winmm=n,b` cargue HelixSR (y que no se cargue sin la opción).
- Que `DXVK_FRAME_RATE` / `VKD3D_FRAME_RATE` limiten Cyberpunk (vkd3d-proton).
- Que la detección `reaper SteamLaunch AppId=` siga el juego (inicio, salida y caída).
- Rendimiento por juego desde STONE OPTIMIZED vía `stoned` (polkit en Game Mode y por SSH, grupo wheel).
- Cyberpunk: la matriz completa con números reales, la recomendación resultante, 30 min jugando y HARDWARE_VERIFIED.
- Potencia de la BC-250 en MangoHud: ¿es fiable?

## Selector de imagen (rc19)

`Engine.image_options(appid)` y `Engine.set_image(appid, key, preset)` (host: `game.image`, `game.setImage`) dan a la persona una elección explícita entre nativo, FSR del juego, FSR de pantalla, HelixSR y OptiScaler. Solo se ofrece lo que `graphics.availability` declara disponible con datos reales; lo demás se rechaza con su motivo. La elección se guarda como configuración CUSTOM. «STONE decide» vuelve al modo recomendado. HelixSR no se elige nunca por la persona sin que lo pida.

## Instalar FSR (OptiScaler) y HelixSR desde STONE (rc20)

- **Descarga:** `install.py` pide la última versión al repositorio oficial (`optiscaler/OptiScaler`, `lonewolf0622/HelixSR`) por HTTPS, solo a los servidores de GitHub, comprueba tamaño y SHA-256 declarados, desempaqueta sin enlaces ni rutas fuera de la carpeta y guarda en `~/.local/share/stone/components/<nombre>/<versión>/`.
- **OptiScaler en un juego:** copia sus archivos a la carpeta del ejecutable (el .exe más grande que no sea lanzador) con `OptiScaler.dll` como `winmm.dll`, **sin sobrescribir nada**; guarda la lista y «Quitar» borra exactamente esos archivos. Después se usa como cualquier OptiScaler: `WINEDLLOVERRIDES=winmm=n,b` solo en ese lanzamiento.
- **HelixSR:** se descarga y STONE muestra el comando del instalador oficial; no lo ejecuta.
- **Nunca** en juegos con anti-cheat o solo online. El botón pide una segunda pulsación para confirmar.
- Terminal: `stone-game components [nombre]`, `stone-game install <appid> optiscaler|helixsr`, `stone-game uninstall <appid>`.
- **Sin verificar en hardware.** La disposición de archivos de cada versión puede cambiar; si no hay `OptiScaler.dll` en la descarga, STONE no instala y lo dice.

## Optimización guiada (rc21)

`Engine.optimize_status(appid)` (host: `game.optimizeStatus`) cuenta cuántas filas de la matriz (etapa 1 y etapa 2) tienen medición vigente; `game.optimizeStart` lanza la siguiente con MangoHud registrando desde el segundo 90 durante 120 s. La interfaz lo muestra como «Optimizar (n/total)» con confirmación en dos pulsaciones. El total puede crecer cuando termina la etapa 1.
