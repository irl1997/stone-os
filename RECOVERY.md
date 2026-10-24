# Recuperación de STONE MACHINE

Qué hacer si STONE MACHINE no arranca bien, una actualización sale mal o no se puede usar la pantalla. Estado: **CODE TESTED, NOT HARDWARE VERIFIED** (ver HARDWARE_VALIDATION.md, pruebas 10–12 y 21–23).

En resumen, de lo más suave a lo más fuerte:

| Problema | Qué hacer |
| --- | --- |
| Un ajuste de rendimiento da problemas | Ajustes → Rendimiento → Restaurar valores de fábrica |
| La consola se cuelga con un ajuste | Nada: al reiniciar vuelve sola a la última configuración buena; dos fallos seguidos → Safe Mode |
| STONE Home no aparece | Steam sigue funcionando: abre «STONE» desde la biblioteca de Steam (ver abajo) |
| Una actualización rompe algo | Reinicia y elige la versión anterior en el menú de arranque (rollback) |
| Nada de lo anterior | Parámetro de arranque `stone.safe=1` o reinstalar desde la ISO (INSTALL.md) |

## Lo que pasa solo

- **Cualquier corte, cuelgue o reinicio** devuelve la GPU a la configuración de fábrica del regulador (equivalente a BALANCED). STONE nunca guarda un ajuste en el regulador.
- **Un perfil CUSTOM sin validar** nunca se aplica al encender.
- **Dos arranques seguidos que no terminan bien** (la consola se cuelga o Home no llega a aparecer) → Safe Mode automático en el siguiente arranque.
- **Safe Mode** = nada por encima de BALANCED, sin perfiles CUSTOM, sin perfiles por juego, sin pruebas de estabilidad. La vigilancia térmica sigue activa. Las ranuras CUSTOM y los perfiles por juego se conservan, sin usarse.

## Activar Safe Mode a mano

| Situación | Cómo |
| --- | --- |
| La pantalla funciona | Ajustes → Rendimiento → Safe Mode → Activar |
| La pantalla no funciona, pero hay otra forma de entrar (modo escritorio, SSH, una terminal) | `sudo stonectl safe-mode on` |
| No se puede entrar de ninguna forma | Arrancar con el parámetro `stone.safe=1` (ver abajo) |

`stonectl` le pide el cambio al servicio `stoned`. Si `stoned` no responde, deja la petición guardada y se aplica en el próximo arranque.

## Salir de Safe Mode

| Cómo entró | Cómo salir |
| --- | --- |
| Automático (dos fallos) o a mano | Ajustes → Rendimiento → Safe Mode → Salir, o «Restaurar valores de fábrica», o `sudo stonectl safe-mode off` |
| Con `stone.safe=1` | Quitar el parámetro de arranque. Desde la interfaz no se puede: así no se sale por error |

## El parámetro `stone.safe=1`

**Permanente, hasta quitarlo** (desde una terminal):

```
sudo rpm-ostree kargs --append-if-missing=stone.safe=1
systemctl reboot
```

Para quitarlo:

```
sudo rpm-ostree kargs --delete-if-present=stone.safe=1
systemctl reboot
```

Qué hace: añade o quita una palabra en la línea de arranque del kernel. No cambia nada más del sistema. Se puede revertir siempre con el segundo comando.

**Una sola vez, sin terminal:** editar la entrada en el menú de arranque y añadir `stone.safe=1` al final de la línea `linux`. [VERIFICAR en placa: cómo se muestra el menú de arranque en la imagen de STONE OS, que lo oculta por defecto]

## Ver el estado

```
stonectl status
```

Muestra el perfil, si Safe Mode está activo y por qué, lo que informa el regulador y el contador de fallos.

## Volver a fábrica del todo

`sudo stonectl restore-defaults`, o «Restaurar valores de fábrica» en Performance Center: BALANCED, sin perfiles por juego, fuera de Safe Mode. Las ranuras CUSTOM se conservan.

Borrar `/var/lib/stone` (con `stoned` detenido) elimina también las ranuras y el historial de pruebas.

## Si `stoned` no arranca

systemd lo reintenta 5 veces en 60 s y luego lo deja. La interfaz muestra «Servicio de rendimiento no disponible» y la GPU sigue con la configuración de fábrica del regulador, que es segura. Ver el motivo:

```
journalctl -u stoned -b
```

## Si STONE Home no aparece

STONE Home es una aplicación más dentro de Steam Game Mode: si falla, Steam sigue funcionando y los juegos también.

1. En Steam, Biblioteca → **STONE** → Jugar. Si se abre, el problema fue sólo el arranque automático.
2. Si no está en la biblioteca: reinicia. La primera vez que se inicia sesión en Steam, STONE se registra en el siguiente arranque (Steam reescribe su lista de accesos directos al cerrarse, así que STONE nunca la toca mientras Steam está abierto).
3. Desde una terminal (modo escritorio o SSH):

```
stone-home-session status
journalctl --user -u stone-home-register -u stone-home-autostart -b --no-pager
```

Para que STONE no se abra solo (y quedarse con la interfaz de Steam): `systemctl --user mask stone-home-autostart.service`. Para volver: `systemctl --user unmask stone-home-autostart.service`.

STONE Home no guarda nada que importe para la consola: perfiles, ranuras y Safe Mode viven en `stoned`. Sus propios ajustes (sonido, tamaño de texto, favoritos) están en `~/.config/stone/home.json`; borrarlo vuelve a los valores por defecto.

## Volver a la versión anterior (rollback)

STONE OS se actualiza como una imagen completa (bootc). La versión anterior queda guardada al lado, intacta.

**Sin terminal:** reinicia y, en el menú de arranque, elige la **segunda** entrada. Arranca la versión anterior completa: sistema, `stoned` y STONE Home. [VERIFICAR en placa: tecla para mostrar el menú de arranque, prueba 22]

**Con terminal**, para que la anterior quede como predeterminada:

```
sudo bootc rollback
systemctl reboot
```

Qué hace: intercambia la versión actual y la anterior en el arranque. No borra nada. Se revierte repitiendo el mismo comando.

Ver qué versiones hay: `rpm-ostree status` (o Ajustes → Actualizaciones en STONE).

## Reinstalar

Si nada de lo anterior sirve, reinstala desde la ISO (INSTALL.md). Borra el disco de sistema.
