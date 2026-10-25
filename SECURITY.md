# Seguridad de STONE OS 0.1.0-rc3

El sistema de overclock necesita privilegios, así que se trata como un componente de seguridad crítica. Resumen de la revisión de esta versión: **0 problemas críticos abiertos**. Los riesgos aceptados están al final.

## Quién puede hacer qué

| Proceso | Usuario | Puede | No puede |
| --- | --- | --- | --- |
| STONE Home (`stone-home`) | El de la consola (nunca root) | Pedir a `stoned` los 17 métodos permitidos; leer la biblioteca de Steam; abrir URLs fijas de Steam; apagar/reiniciar por logind; escribir sus propios ajustes y el acceso directo «STONE» en la carpeta de Steam del usuario | Ejecutar comandos, abrir rutas o URLs elegidas por la página, hablar con el regulador |
| `stoned` | `stoned` (sistema, sin shell) | Llamar al D-Bus del regulador con valores validados; leer sysfs/hwmon y `/proc`; leer el journal del kernel (grupo `systemd-journal`); usar el nodo de render de la GPU para la prueba (grupo `render`) | Escribir en sysfs, tocar voltajes, ventilador o CPU; red (PrivateNetwork); home de usuarios (ProtectHome); escribir fuera de `/var/lib/stone` |
| `stoned-recover` | `stoned` | Leer/escribir el estado en `/var/lib/stone` | Tocar el hardware |
| `stonectl` | Quien lo ejecute; los cambios piden polkit (o dejan una petición para el próximo arranque si `stoned` no responde) | Estado, Safe Mode, valores de fábrica | Ajustes de rendimiento arbitrarios |

## D-Bus y polkit

- Nombre `org.stonemachine.Performance1`: sólo el usuario `stoned` puede poseerlo (`system.d/org.stonemachine.Performance1.conf`).
- Cada método tiene tipos fijos; `stoned` rechaza cualquier llamada cuyo tipo no coincida, antes de leer los argumentos.
- Los métodos de escritura pasan por polkit en cada llamada: `org.stonemachine.performance.change` y `.tune`, permitidos sólo a la **sesión local activa** (la persona frente a la consola). Remoto o inactivo: denegado. Sin polkit disponible: denegado.
- Ningún método recibe una ruta, un comando, un nombre de archivo ni una URL.
- STONE Home acepta señales de `stoned` sólo si vienen del dueño actual del nombre (otro proceso no puede fingir un cambio de estado).

## Validación de entradas

Dos capas: STONE Home comprueba tipo y forma antes de llamar (`bridge.py`); `stoned` vuelve a validar todo contra los rangos de la placa y el estado (`profiles.py`): tipo, mínimo, máximo, múltiplos, rango aceptado por el regulador (`Range/Allowed`), hardware detectado, Safe Mode, prueba en curso y estado térmico. Los umbrales térmicos sólo pueden bajar respecto a los de fábrica; `0,0` (que desactivaría la protección del regulador) se rechaza siempre.

## STONE Home

- La página corre en WebKit sin acceso a red (CSP sin orígenes externos), sin navegación fuera de `stone://home/`, sin ventanas nuevas.
- El esquema `stone://` sólo sirve los archivos que están en `/usr/share/stone-home` (y `fonts/`) y carátulas resueltas a partir de un AppID numérico y un tipo de una lista fija.
- `launch` sólo acepta un AppID que esté en la última lista de la biblioteca; la URL la construye el host.
- Los textos de los archivos de Steam (nombres de juegos) se insertan como texto, nunca como HTML (probado de extremo a extremo con un nombre con etiquetas).
- `~/.config/stone/home.json` se lee con un esquema fijo; claves desconocidas o valores fuera de rango se descartan.
- `shortcuts.vdf`: sólo se escribe con Steam cerrado, de forma atómica, con copia de seguridad la primera vez, y nunca si el archivo no se puede leer.

## systemd

`stoned.service`: `Type=notify` con `WatchdogSec=15`, `Restart=on-failure` (5 en 60 s), `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateNetwork`, `NoNewPrivileges`, sin capacidades, `RestrictAddressFamilies=AF_UNIX`, `DevicePolicy=closed` con sólo `char-drm`, `UMask=0077`. Exposición según `systemd-analyze security`: 3.6 («OK»).

Interruptores de prueba (`STONE_POLKIT=0`, `STONE_BUS`, `STONE_GAME_SOURCE`, `STONE_SYSFS_ROOT`, …): existen en el código sólo para las pruebas automáticas, sólo se pueden fijar desde el entorno del servicio (root), y `scripts/validate.sh` falla si alguna unidad incluida los fija.

## Cadena de suministro

- Todas las acciones de GitHub fijadas por SHA de commit; permisos mínimos declarados en cada flujo y cada trabajo.
- Ningún secreto obligatorio: `GITHUB_TOKEN` publica en GHCR y crea la release. La clave de firma (`SIGNING_SECRET`) es opcional y nunca se escribe en el repositorio (`.gitignore` excluye `cosign.key`).
- Base fijada por digest y registrada; el COPR del regulador se desactiva tras instalar; `bootc container lint` en cada build.
- Las sumas SHA-256 de la ISO y de cada parte se calculan en el build y se verifican antes de publicar la release.

## Riesgos aceptados en RC1

| Riesgo | Por qué se acepta | Mitigación |
| --- | --- | --- |
| Cualquier programa de la sesión activa puede pedir cambios de perfil a `stoned` | Game Mode no tiene diálogo de contraseña; pedirla impediría usar la consola | Los valores siempre se validan contra el rango estable; nada se guarda sin prueba; la protección térmica no se puede desactivar |
| Imagen sin firma si no se configura cosign; la consola actualiza por `registry` sin política de firma | Evita un secreto obligatorio para construir | Configurar `SIGNING_SECRET` (GITHUB_SETUP.md, paso 4); verificar con `cosign verify --key cosign.pub` |
| Paquetes de Fedora y del COPR resueltos en el momento del build | Igual que Bazzite y la plantilla de Universal Blue | Versiones registradas en cada imagen; fijar el regulador tras validarlo en placa |
| El servidor de desarrollo de STONE Home expone la API por HTTP | Sólo para desarrollo y CI | Escucha sólo en 127.0.0.1 y no se instala en la imagen |

Para informar de un problema de seguridad, abre un *security advisory* privado en el repositorio de GitHub (pestaña Security).
