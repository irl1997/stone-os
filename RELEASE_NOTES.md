# STONE OS 0.1.0-rc13

Release Candidate 13 de STONE OS para STONE MACHINE (AMD BC-250), sobre Bazzite `bazzite-deck` (Fedora 44). Corrige lo que mostró la calibración de rc11 en la placa.

**Nada está verificado en hardware todavía.**

## Cambios desde 0.1.0-rc12

- **Calibración, lo visto en la placa (01-11-2026):** el primer paso (1500 MHz con 40 CU) se dio por inestable con la GPU a 48 °C y una frecuencia mediana de 58 MHz.
  - La causa era la prueba de STONE, no la placa. `stone-cu-verify` ocupaba la GPU 7 ms y después el procesador comprobaba durante unos 10 s, así que la GPU pasaba casi todo el tiempo sin trabajo y el regulador bajaba la frecuencia. Además, las 40 CU dieron **0 errores** de cálculo.
- **`stone-cu-verify` mantiene la GPU ocupada:**
  - pasadas largas en la GPU (2048 iteraciones por valor);
  - el procesador comprueba una muestra aleatoria de grupos que cuesta como mucho el 15 % del tiempo de GPU;
  - informa `gpu_busy=` (en el emulador: 87 % del tiempo).
  - `--full` comprueba todo, como antes.
- **Una carga que no usa la GPU ya no se da por falla de la GPU.** Si la actividad mediana queda bajo 50 %, o la GPU se calienta menos de 8 °C cuando no hay lectura de actividad, el paso queda como `load_too_light`. Entonces la calibración queda **no válida**, no «fallida»: no cuenta como falla y conserva el resultado anterior.
- **Diagnóstico:**
  - lo que imprime la carga (vkmark / `stone-cu-verify`) queda en `/var/lib/stone/workload.log`;
  - al fallar un paso, el registro de `stoned` muestra:
    - esas últimas líneas;
    - el contenido de `pp_dpm_sclk`;
    - la subida de temperatura;
    - la actividad mediana de la GPU.
  - `stonectl calibrate` muestra en vivo la frecuencia y la actividad de la GPU.
- La carga corre con `HOME` y caché propios en `/var/lib/stone`, porque el servicio no puede escribir en `/home`.
- `stonectl calibration` dice «no pasó ni la línea base» cuando falla el primer paso, en vez de «se guardó el anterior con margen».
- **Pruebas:** P14 (carga sin GPU → no válida) y P15 (un intento no válido conserva la calibración anterior).
- Incluye rc12 (STONE OPTIMIZED).

## 0.1.0-rc12

Duodécima Release Candidate de STONE OS para STONE MACHINE (AMD BC-250), sobre Bazzite `bazzite-deck` (Fedora 44).

**El software está listo para probar en la placa. Nada está verificado en hardware todavía.** No hay benchmarks en esta versión: el perfil de Cyberpunk 2077 es un punto de partida y una matriz de pruebas, sin números.

## Cambios desde 0.1.0-rc11

- **STONE OPTIMIZED — Game Profile Engine** (`stone_games/`, ver `STONE_OPTIMIZED.md`). JUGAR ahora pasa por el perfil del juego:
  1. pantalla;
  2. perfil;
  3. validación;
  4. gráficos;
  5. rendimiento;
  6. Steam;
  7. vigilancia;
  8. y, al salir, limpieza.
- **STONE GAME PROFILE:** perfiles por AppID de Steam en tres capas: incorporados (imagen, versionados), medidos en esta consola y de la persona.
  - Estados: UNKNOWN, AUTO_GENERATED, RECOMMENDED, USER_MODIFIED y HARDWARE_VERIFIED. Este último solo lo pone la persona con `stone-game verify`.
  - Las mediciones caducan si cambia el juego, Proton, HelixSR/OptiScaler, 40 CU / regulador, la calibración, la pantalla o las reglas.
- **STONE GRAPHICS:** Nativo, FSR/XeSS del juego, HelixSR, OptiScaler y Gamescope (solo orientación).
  - HelixSR/OptiScaler se activan solo para ese lanzamiento con `WINEDLLOVERRIDES`, si la persona los instaló como `winmm.dll`. Las opciones de lanzamiento de la persona se guardan antes y se devuelven después, también si STONE Home o la consola se cayeron.
  - Nunca se inyecta en juegos con anti-cheat u online. HelixSR nunca se fuerza: se prefiere solo cuando, medido, iguala a la mejor experiencia.
  - Fallback: HelixSR → FSR del juego → Nativo, sin restos.
- **STONE OPTIMIZER:**
  - **Medición:** matriz de pruebas medida con MangoHud (sin overlay). Métricas: media, 1 % low, frametime, varianza, reloj, temperaturas y potencia (marcada como no validada).
  - **Puntuación:** STONE EXPERIENCE SCORE documentado, por modo. Objetivos según Hz y VRR de la pantalla, sin la regla fija de 60.
  - **Desempate:** a igual experiencia gana el reloj más bajo.
  - **Etapa 2:** SILENT/PERFORMANCE de las dos mejores.
- **Recuperación:**
  - una caída del juego cuenta el fallo por separado para gráficos y para rendimiento;
  - el siguiente lanzamiento es seguro;
  - con dos caídas, la configuración queda sospechosa.
  - Un perfil por juego elegido a mano en STONE Performance se respeta siempre.
- **API del host:** `game.profile`, `game.recommended`, `game.modes`, `game.apply`, `game.launchOptimized`, `game.status`, `game.restoreRecommended`.
  - Ficha del juego: `STONE RECOMMENDED · … · estado`.
  - Preferencia `optimized` para desactivarlo.
- **`stone-game`** (terminal): status, plan, measure, play, rate, verify, restore, export y recover.
- **Cyberpunk 2077:** primer perfil de referencia (sin mediciones) y CYBERPUNK TEST MATRIX. Procedimiento físico en `HARDWARE_VALIDATION.md` §29.
- **Pruebas:** `tests/test_games.py` (G01–G18). `test-all` aísla `XDG_STATE_HOME`/`XDG_DATA_HOME`.
- Incluye rc11 (STONE PERFORMANCE 2.0) y rc10 (STONE DISPLAY), por si no se publicaron.

## Cambios en 0.1.0-rc11 (desde rc10)

- **STONE PERFORMANCE 2.0** (ver `STONE_PERFORMANCE.md`): se acaba el techo fijo de 1500 MHz con 40 CU. Cada consola tiene su rango:
  - **hardware:** el que permite el regulador;
  - **línea base:** 1500 MHz;
  - **máximo validado:** el que pasó la calibración de esa consola;
  - **actual:** el que informa el regulador.
- **STONE CALIBRATION** (`stonectl calibrate quick|standard`, solo a mano):
  - sube de 50 en 50 MHz y prueba cada paso con `stone-cu-verify` (resultados comprobados) y `vkmark`;
  - se detiene ante el primer error, por temperatura de GPU (90 °C) o de CPU (95 °C), o si el reloj no se sostiene;
  - guarda el último paso aprobado menos 50 MHz de margen, o el máximo del regulador si todo pasa.
  - Un cuelgue durante un paso cuenta como fallo de ese paso al arrancar.
  - El resultado queda ligado a esta máquina y a su configuración (CU, kernel, amdgpu, regulador, VBIOS); si cambia algo, vuelve a la línea base hasta recalibrar.
- **Perfiles:**
  - SILENT = línea base;
  - BALANCED = máximo validado, sin pasar de 1850;
  - PERFORMANCE = hasta el máximo validado;
  - CUSTOM ≤ máximo validado.
  - El regulador sigue escalando el reloj con la carga. La página muestra los valores que da `stoned`, no números fijos.
- **Controlador dinámico** durante los juegos: baja el techo en pasos de 50 MHz cerca del límite de temperatura y lo devuelve al enfriarse. Hay una entrada preparada para el objetivo de cuadros del futuro STONE GAME PROFILE.
- `GetMetrics` añade CU, máximo validado, línea base, techo, margen térmico, estado del regulador y perfil.
- Arreglo: al terminar una prueba se detiene todo el grupo de procesos de la carga, también si el proceso principal ya había terminado.
- Incluye todo lo de rc10 (STONE DISPLAY) por si rc10 no se publicó.

## Cambios desde 0.1.0-rc9

- **STONE DISPLAY** (`stone_display/`, ver `STONE_DISPLAY.md`): detecta la pantalla por el kernel (EDID y modos validados en el enlace, sin root) y por Steam (modo de salida actual, modos, VRR/HDR activos, tamaño que Steam da a los juegos). Separa cinco resoluciones: nativa, salida, la que ven los juegos, interna y final. Lo que no se sabe es DESCONOCIDO, nunca inventado. Escucha los eventos de conexión (udev) en vez de sondear.
- **Juegos a la resolución del televisor, para todos:** los juegos sólo podían ofrecer el tamaño que Steam les da. Con **Ajustes → Pantalla → Juegos ven: Televisor** (por defecto), STONE:
  - sube el tamaño por defecto de Steam a la nativa, comprobándolo y deshaciéndolo si Steam no lo acepta;
  - pone «Nativa» en cada juego que estaba en «Default», también en los desconocidos y recién instalados.
  Lo que la persona eligió a mano no se toca, y **Juegos ven: Steam** lo deshace todo. Ningún caso por juego ni por AppID.
- La salida al televisor no se cambia sola: si está por debajo de la nativa, STONE lo dice y explica dónde cambiarla.
- **Diagnóstico:** `stonectl display`, `stonectl display --json` y `stonectl display diagnostics`. Este último añade la línea de órdenes de Gamescope, los ajustes de sesión y ScopeBuddy, y guarda todo en un archivo para soporte, sin números de serie.
- **Ajustes → Pantalla** muestra Televisor (nativa, VRR, HDR, Hz máximos), Salida y Juegos ven.
- 16 pruebas nuevas cubren:
  - pantallas: 1080p60/120, 1440p60/120, 4K30/60/120, VRR sí/no, HDR sí/no, sin EDID, desconectada, cambio de pantalla y varias salidas;
  - juegos: conocido, desconocido y con tamaño elegido;
  - el tamaño por defecto de Steam: verificado y deshecho si Steam no lo acepta.

## Cambios desde 0.1.0-rc8

- **STONE Home se dibuja con la GPU, a 4K.** La interfaz pasa a Chromium, el mismo motor con el que Steam dibuja su propio modo juego dentro del mismo Gamescope. Con GTK 4 + WebKitGTK, los caminos por GPU parpadeaban y se veían transparentes en la placa (RC2), así que hasta RC8 la interfaz se dibujaba sin GPU, en la CPU, y a 4K eso no puede ser fluido.
- **Resolución:** la página sigue diseñada a 1920 × 1080 y Chromium la dibuja a la resolución real de la ventana (factor 2 en un televisor 4K). Es nítida a 3840 × 2160 y no se escala como imagen. **Ajustes → Pantalla → Resolución de STONE** muestra la resolución real; si Steam le da a STONE menos que la del televisor, ahí se explica cómo poner «Resolución del juego: Nativa».
- **No resta rendimiento a los juegos:** mientras un juego tiene el foco, STONE se duerme: animaciones y transiciones en pausa, no lee el mando, sonido suspendido, sin lecturas en vivo. Chromium no dibuja ningún cuadro hasta que STONE vuelve al frente. Sigue con prioridad de CPU baja (`nice 5`).
- **Seguridad del transporte:** la página habla con su proceso por HTTP sólo en 127.0.0.1, bajo una ruta secreta nueva en cada arranque, comprobando `Host` y `Origin` (`stone_home/server.py`, el mismo del servidor de desarrollo). Chromium arranca con perfil propio, sin extensiones, sin sincronización y con un proxy inexistente para todo lo que no sea 127.0.0.1.
- **Respaldo:** si Chromium falta o se cae tres veces al arrancar, STONE abre la interfaz WebKit sin GPU. También se puede elegir en **Modo de imagen: Sin GPU**. Los modos experimentales de RC7 (WebKit con GL, DMA-BUF y Vulkan) se retiran.
- `home.log` anota al arrancar el motor, la resolución, el factor de escala y la GPU que dibuja (`[stone] draw: 3840x2160 dpr=2 gpu=…`). Si dice SwiftShader o llvmpipe, no está usando la GPU. También anota cuándo STONE se duerme y despierta.
- Pruebas nuevas:
  - la interfaz real en Chromium a 3840 × 2160 contra `stoned`;
  - el transporte (ruta secreta, otro sitio, DNS rebinding, tamaño);
  - el sueño durante el juego (ningún cambio en la página mientras duerme).
- Incluye todo lo de RC8 (40 CU opcional) por si RC8 no se publicó.

## Cambios desde 0.1.0-rc7

- **40 CU de la GPU (opcional, apagado por defecto).** El `amdgpu` de la imagen se recompila en cada build para el kernel exacto de Bazzite (fuente de OpenGamingCollective/linux en el tag del kernel, contra el `kernel-devel` de la misma base) con el desbloqueo de la comunidad (duggasco/bc250-40cu-unlock): sólo actúa con `amdgpu.bc250_cc_write_mode=3` y sólo en la GPU 0x13FE. Sin ese argumento hace lo mismo que el de Bazzite. Si un kernel nuevo no permite construirlo, la imagen sale igual con el `amdgpu` de Bazzite y `stone-cu40 status` lo dice.
- **`stone-cu40`** (a mano, con `sudo`): `enable` pide escribir ACTIVAR, comprueba GPU, temperatura y Modo seguro y añade el argumento del kernel (`rpm-ostree kargs`, nueva entrada de arranque; la anterior queda en el menú). `test` corre `stone-cu-verify` (cálculo en la GPU comprobado contra la CPU, propio de STONE) vigilando temperatura (se detiene a 90 °C), frecuencia y el registro del kernel; si falla, apaga 40 CU solo. `disable` lo quita. Al arrancar, `stone-cu40-guard` apaga 40 CU si el arranque anterior con 40 CU tuvo errores de GPU. Nunca enciende nada solo.
- **Rango estable con 40 CU:** `stoned` limita la GPU a 1500 MHz en todos los perfiles (al arrancar, en cada cambio y cada 10 s por si el regulador se reinicia), los rangos de STONE TUNING se ajustan y el Performance Center lo muestra. El límite de temperatura del regulador no cambia.
- **Ajustes → Rendimiento** muestra «GPU · unidades de cálculo» (24/40 CU y cómo activarlo). **Acerca de** muestra los núcleos que ve el sistema (8 en placas desbloqueadas) en vez de 6 fijos.
- Los 8 núcleos de CPU no necesitan nada de STONE: el desbloqueo vive en el firmware (BIOS) de la placa.

## Cambios desde 0.1.0-rc6

- **Instalar juegos de Steam:** la biblioteca muestra también los juegos de la cuenta que no están instalados (de la interfaz de Steam: `collectionStore.allGamesCollection`). En su ficha el botón es INSTALAR: abre la página del juego en Steam y el diálogo de instalación de Steam (`SteamClient.Installs.OpenInstallWizard`), donde se elige el disco. Sin la interfaz de Steam, la biblioteca sigue mostrando lo instalado.
- **Emuladores:** nueva categoría **Ajustes → Emuladores** con un catálogo fijo de Flathub (RetroDECK, RetroArch, Dolphin, PCSX2, PPSSPP, RPCS3, Cemu, Azahar, melonDS, mGBA, Flycast, xemu). Se instalan para el usuario (`flatpak install --user`, sin contraseña) y se añaden a Steam como accesos directos (`SteamClient.Apps.AddShortcut`; si Steam no responde, el registro los escribe antes del siguiente arranque de Steam). La página sólo envía una clave del catálogo. Juegos y BIOS los aporta la persona.
- **Modo de imagen, sin bajar resolución:** se retira «Ligero». Los modos son Estable (sin GPU) y tres caminos por GPU a resolución completa: GL con redibujado completo, GL con entrega DMA-BUF de WebKit, y Vulkan sin la capa de Gamescope. Nueva fila **Medir fluidez**: STONE recorre el inicio sola unos segundos y mide los cuadros (media, cuadro más lento, cuadros lentos); queda también en `home.log`. Las animaciones de STONE ya sólo animan `transform` y `opacity`; con composición por GPU son baratas, sin ella cada cuadro se pinta entero en la CPU, que es lo que se ve lento en Estable.
- **Listas largas en Ajustes** se desplazan dentro del panel.

## Cambios desde 0.1.0-rc5

- **Perfiles de rendimiento («permiso denegado»), la causa real:** `stoned` corre como el usuario `stoned`, no como root, y polkit sólo deja preguntar por los permisos de otro usuario a root o a un «propietario» de la acción. Cada consulta fallaba antes de llegar a las reglas, así que todo cambio se rechazaba (RC4 y RC5). La política declara ahora `org.freedesktop.policykit.owner = unix-user:stoned` en sus dos acciones, y `stoned` anota en el registro cualquier fallo de polkit. `scripts/validate.sh` lo comprueba. Las pruebas automáticas corren con polkit desactivado y no podían verlo.
- **Ficha del juego:** un nombre largo (tres líneas) se montaba sobre JUGAR. El bloque del título se ancla encima de JUGAR y crece hacia arriba, y los nombres largos usan un tamaño menor (máximo tres líneas).

## Cambios desde 0.1.0-rc4

Con RC4 en la placa (25-10-2026): STONE se veía bien, pero las animaciones no eran fluidas, los ajustes que llevan a Steam no abrían nada y los perfiles de rendimiento decían «no tiene los permisos».

- **Perfiles de rendimiento:** STONE Home corre bajo el gestor de servicios del usuario (la sesión del modo juego), fuera de la sesión de logind, y polkit no lo veía como «sesión activa». Nueva regla `/usr/share/polkit-1/rules.d/50-stone.rules`: el administrador de la consola (grupo `wheel`, el usuario creado al instalar) puede cambiar perfiles y ajustar, y apagar o reiniciar. Es el mismo criterio que las reglas de Bazzite para el modo juego. `stoned` sigue comprobando cada valor contra el rango de fábrica del regulador.
- **Ajustes que llevan a Steam:** abren la sección correspondiente de los ajustes de Steam (Pantalla, Audio, Mandos, Red, Bluetooth, Almacenamiento, Sistema, Energía, Accesibilidad) por la interfaz de Steam (`Navigator.Settings`, como Decky). Si Steam no responde, STONE dice dónde está el ajuste.
- **Juegos:** JUGAR también pide el juego a la interfaz de Steam; la URL queda de respaldo.
- **Animaciones:** nuevo ajuste **Pantalla → Modo de imagen**: *Estable* (sin GPU, resolución completa, el de RC3/RC4), *Ligero* (sin GPU, 1280 × 720 que Gamescope escala; más fluido) y *Fluido* (GPU con redibujado completo de cada cuadro, para evitar el parpadeo de RC2; el más fluido y el que menos carga). STONE se reinicia al cambiarlo. *Fluido* está por confirmar en la placa.
- **Rendimiento de los juegos:** STONE Home y sus procesos corren con prioridad de CPU más baja (`nice 5`), así un juego abierto tiene preferencia.

## Cambios desde 0.1.0-rc3

Con RC3 en la BC-250 (25-10-2026) el parpadeo y la transparencia desaparecieron. Quedaban dos cosas:

- **Apertura automática:** el registro de Steam mostró `ExecuteSteamURL: "steam://rungameid/10954522819597172736"` en cada pedido, con el identificador correcto (el mismo con que Steam abre STONE a mano), y Steam nunca abrió el acceso directo: en el modo juego esa URL no abre accesos directos que no son de Steam. Ahora STONE le pide a la propia interfaz de Steam que lo abra (`SteamClient.Apps.RunGame`, como Decky Loader), por el protocolo de depuración que Steam abre en `127.0.0.1:8080` cuando existe `<Steam>/.cef-enable-remote-debugging` (lo crea el registro antes de que arranque Steam). La URL queda como respaldo. Se puede desactivar con `STONE_HOME_CEF=0` en las unidades. Ver SECURITY.md.
- **Volumen:** los sonidos sólo se oían con la tele al 100 %. Se les suman 20 dB con un limitador: medido en Chromium, navegar pasa de −32,7 a −18,7 dBFS, confirmar de −27,5 a −7,7 dBFS, el inicio de −20,5 a −1,9 dBFS sin saturar.
- La apertura automática espera hasta 5 minutos en vez de 3.
- GitHub: el paso que publica la Release libera disco antes de descargar el instalador (falló en RC3 al descomprimir 7,9 GB).

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
