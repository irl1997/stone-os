# STONE DISPLAY — universal display pipeline (0.1.0-rc11)

Estado: **SOFTWARE VERIFIED · READY FOR HARDWARE TEST**. No está verificado en la BC-250 con un televisor 4K.

STONE DISPLAY es la capa del sistema STONE que detecta la pantalla, normaliza lo que puede hacer y decide el **entorno de salida**. No dibuja nada, no elige la resolución interna de los juegos (eso será STONE GRAPHICS) y no toca la GPU (eso es STONE PERFORMANCE). Orquesta lo que ya existe: kernel (DRM/KMS + EDID), Gamescope y Steam. No hay servidor gráfico propio y nada se escribe en `/usr`.

## 1. Auditoría (octubre 2026)

### Arquitectura actual de la imagen

```
TV ──HDMI── amdgpu (DRM/KMS, EDID) ── Gamescope (--steam, compositor y servidor Wayland)
                                          ├─ Xwayland :0  Steam (y STONE Home, como un juego más de Steam)
                                          └─ Xwayland :1  juegos
Steam decide para cada app el tamaño de su Xwayland (átomo GAMESCOPE_XWAYLAND_MODE_CONTROL);
Proton/Wine ofrece al juego ese tamaño como su resolución máxima.
```

### Configuración de Gamescope (verificada en el código, no en recuerdos)

- **`gamescope-session-plus`** (ChimeraOS, la sesión que usa Bazzite) sólo pasa tamaños si están definidos:
  - `SCREEN_WIDTH`/`SCREEN_HEIGHT` → `-W/-H`, el tamaño de **salida**;
  - `INTERNAL_WIDTH`/`INTERNAL_HEIGHT` → `-w/-h`, el tamaño **anidado** (juego);
  - además `--prefer-output`, `--xwayland-count 2` y `--steam`.
  - Por defecto no hay ninguno de los dos.
- **Gamescope** (`src/main.cpp`): sin `-w/-h`, el tamaño anidado es igual al de salida. Sin `-W/-H`, la salida es el modo que elige para la pantalla (el preferido de su EDID, salvo que Steam pida otro).
- **`steamcompmgr.cpp`**:
  - cuando Steam escribe `GAMESCOPE_XWAYLAND_MODE_CONTROL = [servidor, ancho, alto, superres]`, Gamescope redimensiona ese Xwayland, recortado al tamaño de salida salvo superres;
  - publica la lista de modos de la salida (`GAMESCOPE_DISPLAY_MODE_LIST_EXTERNAL`), el EDID (`GAMESCOPE_DISPLAY_EDID_PATH`) y el estado HDR.
- **ScopeBuddy:** STONE no lo usa. La documentación de Bazzite dice que en el modo juego ScopeBuddy fuerza `SCB_NOSCOPE=1` (no lanza Gamescope). Sólo actúa si un juego tiene `scb` en sus opciones de lanzamiento.
- **STONE (antes de rc10):** no pasaba ningún tamaño a Gamescope ni a Steam. No hay `-w/-h/-W/-H`, `SCREEN_*` ni `SCB_*` en el proyecto.

### Valores de resolución en el proyecto

| Dónde | Valor | Clase | Qué se hizo |
| --- | --- | --- | --- |
| `index.html`, `stone-os-prototipo.html` (`#stage` 1920×1080) | 1920×1080 | REQUIRED (unidades de diseño CSS, no salida) | Nada: Chromium lo dibuja a la resolución real con factor de escala (rc9) |
| `stone_home/chromium.py` `DESIGN_W/H` | 1920×1080 | REQUIRED (misma referencia de diseño) | Nada |
| `stone_home/app.py` límite de `STONE_HOME_SIZE` | ≤ 3840×2160 | HARDCODED | Ampliado a 7680×4320 (es sólo un control de cordura) |
| `stone_home/app.py` `set_default_size(1920, 1080)` | 1920×1080 | LEGACY (respaldo WebKit; pantalla completa igualmente) | Nada |
| `stone_display/edid.py` tabla VIC | varios | REQUIRED (estándar CTA-861) | — |
| `stone_display/model.py` umbral 3840×2160 | 4K | REQUIRED (sólo para avisar «el enlace no da 4K60») | — |
| `catrows.js` texto «3840 × 2160» | 4K | DEVELOPMENT (texto de ayuda) | Sustituido por datos reales de STONE DISPLAY |
| `power_boot.js` `setLine(720, …)` | 720 | no es resolución (posición de THE LINE) | — |

No apareció ningún 1080p forzado por STONE ni por la sesión, ningún supuesto de Steam Deck y ninguna configuración por juego.

## 2. Causa del 1080p en Cyberpunk (con lo que se puede demostrar sin la placa)

El límite no lo pone el juego. El juego ofrece como máximo el tamaño de su Xwayland, y ese tamaño lo decide Steam por juego. Hay dos lugares posibles, y STONE DISPLAY los mide los dos:

1. **Ajuste de Steam:**
   - El «Resolución del juego» de ese juego está en `Default`.
   - El `Default` de Steam para esta pantalla (`game_resolution_override_default` en `CMsgSystemDisplayManagerState`) es menor que el televisor, por ejemplo 1920×1080.
   - Es la causa **más probable**. Clase: **STEAM CONFIGURATION**.
2. **Salida de Steam/Gamescope a 1080p:** el modo automático de Steam eligió 1920×1080, por ejemplo a 120 Hz, en vez de 3840×2160 a 60 Hz. Gamescope recorta cada juego a la salida. Clase: **STEAM CONFIGURATION / GAMESCOPE OUTPUT**.

Cuál de las dos fue queda **demostrado en la placa** con `stonectl display` y un juego abierto:

- **Caso 1:** `B · Salida actual` dice 3840×2160, mientras `Steam por defecto` y `Xwayland :1` dicen 1920×1080.
- **Caso 2:** `B · Salida actual` dice 1920×1080.

Ver la sección 9. Hasta esa prueba es una hipótesis con mecanismo comprobado en el código fuente, no un hecho medido.

Descartado:

| Candidato | Por qué no |
| --- | --- |
| STONE CONFIGURATION | No pasa tamaños (auditoría) |
| SCOPEBUDDY | No se usa |
| GAME-SPECIFIC | El límite está antes del juego |
| DISPLAY/EDID | No se descarta hasta ver `A · Nativa`: si el EDID dijera 1080p, el problema sería el televisor o el cable |

## 3. Cinco resoluciones (nunca se mezclan)

| | Nombre | Fuente | Quién decide |
| --- | --- | --- | --- |
| A | Física (nativa) | EDID: primera temporización detallada; si no hay EDID, el primer modo del kernel | El televisor |
| B | Salida de Gamescope | Steam `DisplayManager.GetState()`: modo actual | Steam, por Gamescope |
| C | Disponible para el juego | Tamaño por defecto de Steam o «Nativa» por juego; medido en el Xwayland de juegos | **STONE DISPLAY (política)** + Steam |
| D | Render interno | El propio juego (escala, FSR, HelixSR…) | STONE GRAPHICS (fase siguiente) |
| E | Salida final | Igual a B | Gamescope |

## 4. Arquitectura

```
STONE Home (página) ── Bridge ── host.py: display.status / display.gamePolicy
                                     │
                                stone_display/
                                  drm.py       /sys/class/drm/card*-*/{status,modes,edid}: sin root, sin ioctl
                                  edid.py      EDID 1.3/1.4 + CTA-861 (VIC, HDMI/HF-VSDB, FreeSync, HDR estático)
                                  steam.py     Steam DisplayManager.GetState (protobuf), Apps.Get/SetAppResolutionOverride,
                                               DisplayManager.SetGamescopeInternalResolution
                                  xwayland.py  tamaño de cada Xwayland de Gamescope (libX11, sólo consulta)
                                  model.py     modelo puro: capacidades, pantalla principal, política, diagnóstico
                                  __init__.py  Display (caché + eventos), GameEnvironment (aplicar/verificar/deshacer)
                                  cli.py       stonectl display [--json | diagnostics]
```

- La página nunca lee Linux: pide `display.status` al host (STONE API).
- Nada necesita privilegios: sysfs es legible por el usuario, y Steam se consulta por su propia interfaz local.

### Modelo `DisplayCapabilities`

Campos de `snapshot()["displays"][i]`:

- **Identidad y conexión:**
  - `connected`, `display_id`: hash de fabricante + producto + nombre, nunca el número de serie;
  - `display_name`, `maker`, `connector`, `card`;
  - `connection_type`: HDMI, DisplayPort, o «DisplayPort → HDMI (adaptador)» si el EDID es HDMI en un conector DP.
- **Resoluciones y modos:** `native_resolution`, `current_resolution`, `available_modes`, `modes_source` (`steam` o `kernel+edid`), `refresh_rates` (a la nativa).
- **VRR y HDR:** `vrr_supported`, `vrr_range` (con su fuente: HDMI 2.1 VRR, FreeSync o rango continuo del EDID), `vrr_active`, `hdr_supported`, `hdr_active`.
- **Enlace HDMI:** `hdmi_max_tmds_mhz`, `hdmi_frl_gbps`.

Lo que ninguna fuente dice es `null` (DESCONOCIDO); nada se inventa.

### Pantalla principal de juego

En este orden:

1. La que Steam está usando: tiene un modo actual. Steam nombra sus pantallas por conector, así que nunca se confunden dos salidas.
2. Si no, la única conectada.
3. Si no, la externa con mayor resolución nativa.

### Caché y eventos

- El modelo se calcula una vez y se guarda.
- Se descarta con cada evento udev `change` del subsistema `drm` (GUdev: conexión, desconexión, cambio de EDID o de modo) y cuando se pide «fresco».
- No hay sondeo: nada de leer el EDID cada cuadro ni de lanzar comandos en bucle.
- En el registro (`~/.local/state/stone/home.log`) quedan unas pocas líneas sólo cuando la situación cambia:
  - `DISPLAY_CONNECTED`, `CAPABILITIES_DETECTED`, `OUTPUT_MODE`, `GAME_ENVIRONMENT`;
  - `GAME_ENVIRONMENT_CREATED`, `DISPLAY_CHANGED`, `DISPLAY_DISCONNECTED`.

## 5. Política de salida (B)

`preferred_mode()`:

- **Resolución:** la nativa del televisor. Nunca una resolución menor a cambio de más Hz.
- **Frecuencia:** la mayor que **esta conexión** admite a esa resolución, hasta 144 Hz:
  - la lista de modos de Steam son modos ya validados por el kernel en el enlace;
  - sin Steam, se usan las resoluciones que acepta el kernel con las frecuencias del EDID.
- **Ejemplos:** 4K30 nunca gana a 4K60; 4K120 sólo si Steam lo lista.
- **Lo que no hace:** STONE **no cambia la salida por su cuenta**. Si la salida actual está por debajo de la nativa, o a menos Hz de los posibles, lo informa (`OUTPUT_BELOW_NATIVE`, `REFRESH_BELOW_BEST`) y dice dónde se cambia en Steam.
- **Por qué no la cambia:** un modo de salida malo puede dejar la pantalla en negro, y cambiarlo exige aplicar → verificar en pantalla → confirmar con una persona delante. Eso se hará cuando la placa confirme cómo responde `DisplayManager.SetMode`.

## 6. Entorno de juego (C): la corrección universal

Preferencia `game_res` (Ajustes → Pantalla → **Juegos ven**). Por defecto: **Televisor**.

1. **Para todo el sistema** (`GameEnvironment.ensure_default`):
   - si el tamaño que Steam da por defecto a los juegos es menor que la nativa, STONE llama a `SteamClient.System.DisplayManager.SetGamescopeInternalResolution(nativa)` y **vuelve a leer** el estado de Steam;
   - si el tamaño por defecto no pasó a ser el nativo, **pone el valor anterior de vuelta** y no lo intenta más;
   - cubre los juegos que se abran desde cualquier sitio, también desde la biblioteca de Steam.
2. **Por juego** (`GameEnvironment.apply`):
   - a cada juego instalado y acceso directo cuyo «Resolución del juego» siga en `Default`, STONE le pone `Native`;
   - se hace al leer la biblioteca y justo antes de abrir un juego desde STONE, así cubre también juegos recién instalados;
   - un tamaño que la persona eligió nunca se toca;
   - los ids cambiados se guardan en `~/.local/state/stone/game-resolution.json`.
3. **«Juegos ven: Steam»** deshace las dos cosas: vuelve a `Default` sólo lo que STONE cambió y repone el tamaño por defecto anterior.

Un juego **desconocido**, sin perfil STONE y sin nada configurado, recibe lo mismo que uno conocido. No hay ningún caso por AppID (la prueba D15 lo comprueba en todo el código).

Esto no fuerza 4K en el render: el juego **puede ofrecer** la resolución del televisor y escoge su render interno (D). La estrategia de render será de STONE GRAPHICS (nativo, FSR, HelixSR, OptiScaler).

## 7. Modo seguro de pantalla, persistencia y deshacer

- **Modo seguro de pantalla:**
  - cuándo: si no hay EDID o no hay pantalla, `safe_display_mode = true`;
  - qué hace: no se fuerza nada, ni salida, ni HDR, ni VRR, ni 4K120; se deja lo que eligió Gamescope y la consola sigue mostrando imagen.
- **HDR y VRR:** se informan como *compatibles* y *activos* por separado. STONE nunca los activa.
- **Último estado bueno:**
  - la única escritura en Steam que no es por juego (el tamaño por defecto) se verifica y se deshace sola si no quedó como se esperaba;
  - no se guarda nada que no se haya verificado;
  - los cambios por juego se deshacen con «Juegos ven: Steam».
- **Persistencia:**
  - los ajustes de Steam viven en la configuración de Steam del usuario, y la lista de STONE en `~/.local/state/stone`;
  - sobreviven a reinicios, a `bootc upgrade` y a `bootc rollback`, porque `/home` no cambia;
  - la imagen no escribe nada en `/etc` ni en `/usr` para esto.
- **Arranque:**
  - Plymouth y el kernel usan el modo preferido de la pantalla (KMS);
  - Gamescope arranca con el modo preferido salvo que Steam tenga guardado otro;
  - STONE Home se dibuja a la resolución de su ventana (rc9);
  - con la política «Televisor», STONE Home, Steam y los juegos ven el mismo tamaño.
- **Desconexión durante un juego:**
  - lo gestionan Gamescope y Steam: el juego sigue a su tamaño y Gamescope escala a la nueva salida;
  - STONE recibe el evento udev, rehace el modelo y lo anota (`DISPLAY_CHANGED`);
  - riesgo conocido: un juego abierto no cambia su tamaño hasta que se vuelve a abrir.

## 8. Diagnóstico

```
stonectl display               resumen (A, B, C, D, E, VRR, HDR, Xwayland, hallazgos)
stonectl display --json        el modelo completo
stonectl display diagnostics   además: línea de órdenes de Gamescope, modos guardados por Gamescope,
                               variables de sesión (SCREEN_*, INTERNAL_*, …), archivos de ScopeBuddy;
                               guardado en ~/.local/state/stone/display-diagnostics.txt
```

Se ejecutan como el usuario de la consola, sin `sudo`. No incluyen números de serie.

## 9. Prueba en la placa (BC-250 + televisor 4K)

En `HARDWARE_VALIDATION.md` §27 y en la guía de rc10. En resumen:

1. Con STONE abierta: `stonectl display` → anotar A (nativa), B (salida), «Steam por defecto», «Juegos ven».
2. Abrir **Cyberpunk 2077** (juego de validación 1) y mirar sus resoluciones disponibles.
3. Sin cerrarlo, en la terminal: `stonectl display diagnostics` → `Xwayland :1` debe ser la nativa.
4. Lo mismo con otro juego con motor distinto (juego 2) y con un juego recién instalado sin abrir nunca (juego desconocido).
5. Para la evidencia de la causa: poner **Juegos ven: Steam**, abrir Cyberpunk, `stonectl display diagnostics`; volver a **Televisor** y repetir.

## 10. Límites conocidos

- La API de Steam (`DisplayManager`, `Apps.*ResolutionOverride`) no está documentada. STONE la usa como Decky y comprueba todo lo que cambia. Si Steam la cambia, STONE lo informa y no rompe nada.
- La salida (B) no se cambia automáticamente en esta fase (ver §5).
- HDR y VRR sólo se detectan; activarlos será otra fase con validación.
- Si Steam no responde, B y el tamaño por defecto quedan DESCONOCIDOS; A sale del kernel igualmente.
