# STONE OS 0.1.0-rc1 — Validación en la AMD BC-250

Todo lo de STONE está **CODE TESTED** (pruebas automáticas contra un regulador simulado, un Steam simulado y el navegador); nada está **HARDWARE VERIFIED** hasta que su fila de la matriz final pase en una BC-250 real. **Todas las filas empiezan en NOT TESTED.** No marques PASS sin haber hecho la prueba.

**Reglas de estas pruebas**

- Nada de este documento cambia voltajes, escribe en `pp_od_clk_voltage` ni toca el SMU directamente. Todo pasa por el regulador y sus límites de fábrica (1000–2000 MHz).
- La protección térmica nunca se desactiva.
- Cada comando indica qué hace, cómo revertirlo, su riesgo y qué se espera ver.
- Apunta el resultado real en la tabla final. Si una prueba no se hizo, queda NOT TESTED.

**Antes de empezar**

- BIOS P3.00 con los ajustes de `docs/FIRMWARE.md`, CMOS borrada.
- STONE OS instalado desde la imagen.
- Ventilador en el cabezal principal y la carcasa cerrada como se usará.
- Temperatura ambiente anotada.
- Acceso a una terminal (modo escritorio o SSH desde otro equipo).
- Un mando conectado (USB o Bluetooth) y, para la parte de instalación, un pendrive de 16 GB o más.

---

## 0. Inventario (sólo lectura, riesgo nulo)

Ninguno de estos comandos cambia nada.

```
cat /usr/lib/stone-release
uname -r
lspci -nn | grep -iE 'vga|display'
rpm -q cyan-skillfish-governor-smu vkmark python3-gobject webkitgtk6.0
systemctl status cyan-skillfish-governor-smu stoned-recover stoned --no-pager
stonectl status
```

| Qué comprobar | Esperado |
| --- | --- |
| `STONE_VERSION` en `stone-release` | `0.1.0-rc1` |
| `STONE_GOVERNOR_VERSION` y `STONE_GOV_RANGE` en `stone-release` | Versión anotada; rango `1000-1850` |
| Kernel | Fuera de 6.15.0–6.15.6 y 6.17.8–6.17.10 |
| ID PCI de la GPU | `1002:13fe`. **Si es otro**, anótalo: `stoned` no reconocerá la placa y desactivará el modo avanzado. Hay que añadir ese ID a `CYAN_SKILLFISH_IDS` en `backend_bc250.py` |
| Los tres servicios | `active` (stoned-recover: `active (exited)`) |
| `stonectl status` | Perfil `balanced`, Safe Mode `no`, regulador `1000–1850 MHz, límite 85 °C` |

**Interfaz D-Bus del regulador** (lectura):

```
busctl --system introspect com.cyanskillfish.Governor /com/cyanskillfish/Governor
busctl --system introspect com.cyanskillfish.Governor /com/cyanskillfish/Governor/Range/Current
```

Esperado: métodos `SetRange`, `SetTemperatureThresholds`, `SetLoadTarget`; propiedades de rango `min`/`max` o `Min`/`Max` (`stoned` acepta las dos). Si falta algo, anótalo: es lo primero que hay que corregir.

**Sensores:**

```
sensors
cat /sys/class/drm/card*/device/pp_dpm_sclk
xxd -s 0x1c -l 2 /sys/class/drm/card*/device/gpu_metrics
```

Esperado: `amdgpu` con `edge` y `PPT`, `k10temp` con `Tctl`, y un bloque `nct6686`/`nct6683` con `fan2` girando. Los dos bytes de `gpu_metrics` no deben ser `ffff` con el regulador activo (si lo son, el uso de GPU aparecerá como «—»).

**Permisos de `stoned`:**

```
sudo -u stoned journalctl -k -n 3 --no-pager
```

Esperado: tres líneas del registro del kernel. Si dice que no tiene permiso, las pruebas de estabilidad nunca pasarán (por diseño): revisar que `stoned` está en el grupo `systemd-journal`.

---

## 1. BOOT

Encender 5 veces desde apagado. Tras cada arranque: `stonectl status`.

- **Esperado:** Home en pantalla, perfil `balanced`, fallos seguidos `0`.
- **Riesgo:** ninguno.

## 2. IDLE

30 min en Home sin tocar nada. Al final:

```
cat /sys/class/drm/card*/device/pp_dpm_sclk
top -b -n 1 -p "$(pgrep -d, -f /usr/bin/stoned)"
```

- **Esperado:** la GPU en su frecuencia baja, `stoned` cerca de 0 % de CPU, la consola no se suspende.
- **Riesgo:** ninguno.

## 3. Carga de la prueba de estabilidad

Comprueba que `vkmark` funciona sin pantalla con el usuario de `stoned`, antes de usarlo desde la interfaz.

```
sudo -u stoned vkmark --winsys headless --run-forever
```

Dejarlo 60 s y pararlo con **Ctrl+C**.

- **Qué hace:** carga la GPU con el perfil actual (BALANCED). No cambia ningún ajuste.
- **Revertir:** Ctrl+C.
- **Riesgo:** bajo; es como un juego exigente.
- **Esperado:** corre sin errores. Si falla por `--winsys headless`, anotar el error: hay que cambiar el comando en `/usr/lib/stone/stability.json`.

## 4. GAME y detección de Steam

Abrir un juego exigente desde Steam y, con el juego abierto:

```
ps -eo args | grep [S]teamLaunch
stonectl status
```

- **Esperado:** una línea `…/reaper SteamLaunch AppId=<número> -- …`. Si el formato es otro, anotarlo exacto: `games.py` depende de él.
- Jugar 30 min con cada perfil (SILENT, BALANCED, PERFORMANCE), elegido antes en Performance Center.
- **Esperado:** la frecuencia máxima nunca pasa el límite del perfil; la temperatura queda bajo el umbral; sin cuelgues.
- **Riesgo:** bajo (perfiles dentro del rango de fábrica).

## 5. PROFILE SWITCH

Desde Performance Center, y durante un juego desde el menú de STONE (volver a STONE con el botón STEAM, luego ☰ → Rendimiento), cambiar de perfil. Después de cada cambio:

```
busctl --system get-property com.cyanskillfish.Governor /com/cyanskillfish/Governor/Range/Current com.cyanskillfish.Governor.Range Max
```

(o `max` si la propiedad está en minúsculas)

- **Esperado:** el valor coincide con lo que muestra STONE (1500, 1850 o 2000).
- **Riesgo:** ninguno.

## 6. OVERCLOCK (dentro del rango de fábrica)

1. STONE TUNING → **Medir el perfil actual** (BALANCED).
2. Ranura CUSTOM 01 a **2000 MHz, 85 °C** → Revisar y probar → **Prueba rápida**.
3. Repetir con la **Prueba estándar** (30 min) y guardar.
4. Usar CUSTOM 01 y jugar una partida de 10 min o más; cerrar el juego.

- **Esperado:** la comparación BEFORE / AFTER muestra un veredicto coherente con las lecturas; la ranura queda «provisional» tras la prueba y «validado» sólo tras la partida.
- **Revertir:** «Restaurar valores de fábrica».
- **Riesgo:** bajo: 2000 MHz está dentro de la curva de fábrica del regulador (960 mV).

## 7. THERMAL

STONE TUNING → ranura CUSTOM 02 con límite **65 °C** → Prueba rápida. Mientras corre:

```
watch -n 1 "sensors | grep -E 'edge|PPT'; cat /sys/class/drm/card*/device/pp_dpm_sclk"
```

- **Esperado:** al llegar a 65 °C la frecuencia baja y la temperatura se sostiene; STONE muestra el límite y la frecuencia real.
- **Riesgo:** ninguno: el límite sólo baja.

## 8. LONG SESSION

3 h de juego con PERFORMANCE. Al final: `journalctl -k -b | grep -i amdgpu | tail`.

- **Esperado:** sin cuelgues ni errores de amdgpu; memoria de `stoned` estable (`ps -o rss -p $(pgrep -f /usr/bin/stoned)` al principio y al final).

## 9. REBOOT

Con una ranura validada activa, reiniciar desde STONE.

- **Esperado:** arranca en BALANCED y aplica la ranura sólo cuando Home está en pantalla (`stonectl status` antes y después de abrir Home).

## 10. CRASH RECOVERY

Durante una prueba rápida de una ranura CUSTOM:

```
sudo systemctl kill -s KILL stoned
```

- **Qué hace:** mata el servicio como si se cayera. systemd lo reinicia solo.
- **Revertir:** nada; se recupera solo.
- **Riesgo:** ninguno.
- **Esperado:** en segundos, `stonectl status` muestra el perfil anterior y el regulador vuelve a su rango (prueba 5). La interfaz avisa de que restauró la configuración.

## 11. POWER LOSS

Durante una prueba rápida de una ranura CUSTOM, **cortar la corriente**. Encender. Repetir una segunda vez.

- **Riesgo:** bajo para la placa; corte brusco del sistema de archivos (el sistema de STONE OS es atómico y lo tolera). No hacerlo durante una actualización.
- **Esperado:** primer arranque, la prueba se descarta y vuelve la última configuración buena; segundo arranque, **Safe Mode** con el diálogo correspondiente.

## 12. SAFE MODE

1. Ajustes → Rendimiento → Safe Mode → Activar. Reiniciar. Salir desde el mismo sitio.
2. Desde una terminal: `sudo stonectl safe-mode on`, reiniciar, `sudo stonectl safe-mode off`.
3. Con el parámetro de arranque:

```
sudo rpm-ostree kargs --append-if-missing=stone.safe=1
systemctl reboot
```

- **Esperado:** Safe Mode activo con motivo «pedido en el arranque»; la interfaz no permite salir.
- **Revertir:**

```
sudo rpm-ostree kargs --delete-if-present=stone.safe=1
systemctl reboot
```

4. Averiguar cómo mostrar el menú de arranque en esta imagen (para añadir `stone.safe=1` una sola vez) y anotarlo en `RECOVERY.md`.

## 13. FAN READ

Con la consola en reposo en Home:

```
watch -n 1 "sensors | grep -iE 'fan|edge'"
```

Desconectar el ventilador **30 s** y volver a conectarlo. Si la GPU pasa de 70 °C antes, reconectarlo de inmediato.

- **Esperado:** `fan2` cae a 0 RPM; si la GPU llega a 70 °C con el ventilador a 0, STONE avisa y baja a SILENT.
- **Riesgo:** bajo en reposo y vigilando la temperatura. No hacerlo jugando.

## 14. SUSPEND

Dejar la consola 2 h sin tocar en Game Mode.

- **Esperado:** no se suspende y responde al mando. `systemctl status sleep.target` muestra `masked`.

## 15. HW WATCHDOG (sólo información)

```
ls -l /dev/watchdog* 2>/dev/null; lsmod | grep -i sp5100
```

- **Qué hace:** sólo mira si existe un watchdog de hardware. No lo activa.
- **Esperado:** anotar si existe. Con eso se decidirá si un cuelgue de GPU puede reiniciar la consola sola.

## 16. INSTALADOR Y PRIMER ARRANQUE

1. Grabar la ISO en un pendrive (INSTALL.md) y arrancar la BC-250 desde él.
2. Instalar en el SSD interno siguiendo INSTALL.md (usuario, disco).
3. Primer arranque: pantalla negra con la línea (Plymouth), Steam Game Mode, inicio de sesión de Steam.
4. Reiniciar una vez (desde el botón STEAM → Energía → Reiniciar).

- **Esperado:** tras el reinicio, Steam abre **STONE** solo, sin teclado ni terminal. `stonectl status`: BALANCED, 0 fallos. `stone-home-session status`: `registered: true`.
- **Transición:** la línea de Plymouth continúa en el vídeo de STONE (sin el vídeo de Steam) y sin salto de posición ni de grosor. Anotar si se ve bien en la TV (escala, bordes).
- **Vídeo de 10 s, anotar:** ¿Steam lo reproduce entero o lo corta cuando termina de cargar? ¿STONE Home aparece durante el vídeo (la línea sigue y aparece STONE), o sólo al terminar? ¿Se ve el inicio de Steam en algún momento? Si Steam espera siempre al final y STONE ya estaba listo antes, acortar el vídeo; si todavía se ve Steam, alargarlo (`SECONDS` en `development/make_boot_video.mjs`).
- **Tiempos** (5 arranques), de la línea que deja cada arranque:

```
journalctl --user -u stone-home-autostart -b --no-pager | grep home_after
```

Anotar `steam_ui_after`, `requests` y `home_after`, y cuántos segundos se ve el inicio de Steam antes de STONE (cronómetro). **Ajuste:** si `requests` es siempre 1, se puede bajar `STONE_HOME_SETTLE_S` (por ejemplo a 1); si a menudo es 3 o más, subirlo. El valor final se fija en `stone-home-autostart.service` y se vuelve a medir.
- **Riesgo:** el instalador borra el disco elegido.

## 17. STONE HOME DENTRO DE GAME MODE

```
stone-home-session status
journalctl --user -u stone-home-register -u stone-home-autostart -b --no-pager
```

- Con el mando: Inicio → Biblioteca → ficha de un juego → JUGAR. El juego se abre. Al salir del juego (menú del propio juego o botón STEAM → Salir del juego), **STONE Home vuelve al frente solo**.
- En la ficha de un juego en curso, REANUDAR lo trae al frente. [Comportamiento de Steam a confirmar aquí]
- Biblioteca: carátulas e imágenes de fondo de los juegos instalados; un juego recién instalado aparece al volver a STONE.
- Explorar → STEAM STORE / DESCARGAS y Media → CAPTURAS abren la vista correspondiente de Steam.
- **Esperado:** todo con el mando (cruceta, stick, A, B, LB, RB, ☰). Sin ratón ni teclado.
- **Si el mando no responde dentro de STONE:** probar en Steam → STONE → Controlador → plantilla «Gamepad»; anotar qué plantilla funciona (hay que fijarla en la imagen).
- **Riesgo:** ninguno.

## 18. PERFIL POR JUEGO DESDE STONE HOME

1. Ficha de un juego → «Rendimiento: Global» → elegir SILENT.
2. JUGAR. Con el juego abierto: `stonectl status` y la propiedad `Max` del regulador (prueba 5).
3. Cerrar el juego.

- **Esperado:** durante el juego `Max = 1500`; al cerrar vuelve al perfil global. STONE Home **no** aparece como juego en `stonectl status`.

## 19. APAGAR Y REINICIAR DESDE STONE

Inicio → Apagar → APAGAR. Encender. Repetir con Reiniciar.

- **Esperado:** la animación de la línea y la consola se apaga / reinicia. `stonectl status` en el siguiente arranque: 0 fallos (apagado limpio registrado).

## 20. APAGAR TRAS INACTIVIDAD

Ajustes → Energía → Apagar tras inactividad: 15 min. Dejar STONE Home sin tocar. Repetir con un juego abierto.

- **Esperado:** se apaga a los 15 min en Home; con un juego abierto **no** se apaga.

## 21. ACTUALIZACIÓN ATÓMICA

Publicar una versión nueva en el canal (`rc`) y en la consola: botón STEAM → Ajustes → Sistema → Buscar actualizaciones (o `sudo bootc upgrade`). Reiniciar.

- **Esperado:** Ajustes → Actualizaciones muestra «Actualización lista» antes de reiniciar y la versión nueva después. `rpm-ostree status` muestra la anterior como rollback.

## 22. ROLLBACK

Reiniciar y, en el menú de arranque, elegir la segunda entrada (o `sudo bootc rollback` y reiniciar).

- **Esperado:** arranca la versión anterior completa (sistema, stoned y STONE Home). Ajustes → Acerca de muestra la versión anterior.
- **Revertir:** reiniciar eligiendo la primera entrada.
- **Anotar:** cómo se muestra el menú de arranque en esta imagen (tecla y momento).

## 23. AUTOARRANQUE QUE FALLA

```
systemctl --user mask stone-home-autostart.service
```

Reiniciar. Luego `systemctl --user unmask stone-home-autostart.service`.

- **Esperado:** Steam Game Mode arranca normal; STONE sigue en la biblioteca de Steam como «STONE» y se abre a mano. Nada se cuelga.

## 24. PLACA DISTINTA O SIN REGULADOR (opcional, otro PC o VM)

Instalar la misma imagen en un PC que no sea BC-250 (o una VM).

- **Esperado:** STONE Home arranca; Ajustes → Acerca de dice «No se detectó una AMD BC-250»; Performance Center muestra «Regulador de GPU no detectado» y CUSTOM no está disponible.

---

## Matriz de resultados

| Prueba | Esperado | Resultado real | Estado |
| --- | --- | --- | --- |
| 0 Inventario | Servicios activos, ID PCI, D-Bus, sensores, permisos | | NOT TESTED |
| 1 BOOT | Home, BALANCED, 0 fallos (×5) | | NOT TESTED |
| 2 IDLE | GPU baja, `stoned` ~0 %, sin suspender | | NOT TESTED |
| 3 Carga de la prueba | vkmark sin pantalla funciona como `stoned` | | NOT TESTED |
| 4 GAME y Steam | `reaper SteamLaunch AppId=`; perfiles respetados | | NOT TESTED |
| 5 PROFILE SWITCH | Regulador = lo que muestra STONE | | NOT TESTED |
| 6 OVERCLOCK | Comparación coherente; validación sólo tras partida | | NOT TESTED |
| 7 THERMAL | Se sostiene en 65 °C | | NOT TESTED |
| 8 LONG SESSION | 3 h sin errores de amdgpu | | NOT TESTED |
| 9 REBOOT | BALANCED al arrancar, ranura con Home | | NOT TESTED |
| 10 CRASH RECOVERY | Restaura la última buena | | NOT TESTED |
| 11 POWER LOSS | Descarta, luego Safe Mode | | NOT TESTED |
| 12 SAFE MODE | Tres entradas y salidas | | NOT TESTED |
| 13 FAN READ | 0 RPM detectado, SILENT si calienta | | NOT TESTED |
| 14 SUSPEND | No se suspende | | NOT TESTED |
| 15 HW WATCHDOG | Existe / no existe | | NOT TESTED |
| 16 INSTALADOR Y PRIMER ARRANQUE | STONE se abre solo tras el primer reinicio; vídeo continúa la línea; tiempos anotados | | NOT TESTED |
| 17 STONE HOME EN GAME MODE | Biblioteca, JUGAR, vuelta automática, mando | | NOT TESTED |
| 18 PERFIL POR JUEGO | 1500 MHz durante el juego, global al salir | | NOT TESTED |
| 19 APAGAR / REINICIAR | Animación + apagado limpio registrado | | NOT TESTED |
| 20 APAGAR TRAS INACTIVIDAD | Sí en Home, no con juego | | NOT TESTED |
| 21 ACTUALIZACIÓN | Nueva versión tras reiniciar | | NOT TESTED |
| 22 ROLLBACK | Arranca la versión anterior | | NOT TESTED |
| 23 AUTOARRANQUE QUE FALLA | Game Mode normal, STONE a mano | | NOT TESTED |
| 24 SIN BC-250 | Todo «no disponible», nada se aplica | | NOT TESTED |
