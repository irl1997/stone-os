# STONE OS

El sistema de **STONE MACHINE**: una consola construida sobre la **AMD BC-250** que se usa como una consola, no como un PC con Linux.

STONE OS es una imagen derivada de **Bazzite** (variante `bazzite-deck`, con Steam Game Mode) que añade una capa propia:

```
Bazzite (Fedora Atomic, kernel, Mesa, Steam, Gamescope)
  └─ STONE system layer   stoned · regulador de GPU de la BC-250 · recuperación · Plymouth · sin reposo
      └─ STONE Home       la interfaz de la consola, dentro de Steam Game Mode
          = STONE OS
```

**Versión:** `0.1.0-rc7` (Release Candidate 7) · **Hardware:** AMD BC-250 (BIOS P3.00) · **Base:** `ghcr.io/ublue-os/bazzite-deck:stable-44`, fijada por digest en cada build.

## Estado

| | |
| --- | --- |
| Software | Completo para RC7. Todas las pruebas automáticas pasan (`scripts/test-all.sh`). |
| GitHub Actions | Flujos creados y validados con actionlint. **Aún no ejecutados en GitHub**: la primera ejecución será en tu repositorio. |
| Hardware | **NOT TESTED.** Nada se ha probado todavía en una BC-250 real. Plan completo en [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md). |

Lo implementado, lo probado y las limitaciones conocidas están en [RELEASE_NOTES.md](RELEASE_NOTES.md).

## Qué hace

- **Arranca como consola:** pantalla negra con la línea blanca (Plymouth), Steam Game Mode y STONE Home, sin escritorio, sin teclado.
- **STONE Home:** inicio con los juegos recientes, biblioteca de Steam (incluidos los accesos directos de otros lanzadores y emuladores), ficha de juego, menú rápido, ajustes, energía. Se maneja entero con el mando.
- **STONE Performance:** perfiles SILENT, BALANCED (predeterminado), PERFORMANCE y CUSTOM, aplicados a través del regulador de GPU de la BC-250. La interfaz muestra siempre lo que el regulador informa, no lo que se pidió.
- **CUSTOM con red de seguridad:** cada ajuste se prueba (vkmark) antes de guardarse, se compara con el perfil actual y sólo se valida tras una partida sin errores. Última configuración buena, contador de fallos y Safe Mode.
- **Perfiles por juego**, aplicados al detectar el juego en Steam y retirados al cerrarlo, aunque se cuelgue el juego, Steam o la interfaz.
- **Vigilancia térmica** por encima de las protecciones del hardware (nunca las sustituye).
- **Actualizaciones atómicas** con vuelta atrás a la versión anterior.

Lo que STONE OS **no** hace, a propósito: cambiar voltajes, desbloquear núcleos o CU, desactivar protecciones térmicas, aplicar un ajuste sin probar al arrancar.

## Construir

GitHub construye todo: sube el repositorio y GitHub Actions publica la imagen en `ghcr.io/<tu-usuario>/stone-os` y genera la ISO instalable. Paso a paso, sin experiencia previa: **[GITHUB_SETUP.md](GITHUB_SETUP.md)**. Comandos exactos y build local en Linux: [BUILD.md](BUILD.md).

## Instalar

BIOS, pendrive, instalador y primer arranque: **[INSTALL.md](INSTALL.md)**. La configuración de BIOS de la placa está en [docs/FIRMWARE.md](docs/FIRMWARE.md).

## Actualizar

La consola sigue el canal con el que se instaló (`rc` para esta versión). Cada versión nueva publicada en ese canal llega como una imagen completa y se aplica al reiniciar: botón STEAM → Ajustes → Sistema → Buscar actualizaciones. Ajustes → Actualizaciones en STONE muestra si hay una lista.

## Recuperar

Safe Mode, valores de fábrica, fallo de `stoned` o de STONE Home y vuelta a la versión anterior: **[RECOVERY.md](RECOVERY.md)**.

## Documentación

| Documento | Contenido |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Cadena completa del arranque al apagado, componentes, estructura del repositorio, decisiones |
| [BUILD.md](BUILD.md) | Construir la imagen y la ISO (GitHub y local) |
| [GITHUB_SETUP.md](GITHUB_SETUP.md) | Guía de GitHub paso a paso |
| [INSTALL.md](INSTALL.md) | Instalar en la BC-250 |
| [RECOVERY.md](RECOVERY.md) | Recuperación |
| [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) | Pruebas en la placa (todas NOT TESTED) |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | Qué trae 0.1.0-rc7 y sus limitaciones |
| [DEPENDENCIES.md](DEPENDENCIES.md) | Dependencias, versiones y cómo se actualizan |
| [SECURITY.md](SECURITY.md) | Modelo de seguridad y privilegios |
| [docs/STONED.md](docs/STONED.md) | El servicio `stoned` y su API D-Bus |
| [docs/STONE_HOME.md](docs/STONE_HOME.md) | STONE Home: shell, puente, biblioteca, integración con Steam |
| [docs/AUDIT_STATUS.md](docs/AUDIT_STATUS.md) | Estado de cada problema de la auditoría (P0–P3) e inventario de archivos |
| [docs/FIRMWARE.md](docs/FIRMWARE.md) | BIOS de la BC-250 |

## Licencia

Código de STONE OS: Apache-2.0 ([LICENSE](LICENSE)). Fuentes tipográficas incluidas (Jost, Inter, IBM Plex Mono): SIL Open Font License 1.1, licencias junto a cada fuente en `system_files/usr/share/stone-home/fonts/`. Bazzite, Steam y el resto de componentes de la base mantienen sus propias licencias.
