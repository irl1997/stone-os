# Instalar STONE OS 0.1.0-rc2 en la AMD BC-250

Estado: **NOT TESTED en placa.** Estos pasos siguen el instalador estándar de bootc-image-builder (Anaconda) y la documentación de la BC-250; la prueba 16 de HARDWARE_VALIDATION.md los comprueba en la consola real.

> El instalador **borra el disco que elijas**. Desconecta cualquier otro disco que no quieras tocar.

## Qué necesitas

- La BC-250 montada y alimentada como indica [docs/FIRMWARE.md](docs/FIRMWARE.md) (sólo 12 V por PCIe de 8 pines, fuente de 300 W o más).
- Monitor o TV por **DisplayPort** (la placa no tiene HDMI), teclado USB sólo para la instalación, mando para después.
- Cable de red (la placa no tiene Wi-Fi ni Bluetooth integrados; para mando Bluetooth, un adaptador USB).
- SSD interno (NVMe o SATA según tu montaje) de 128 GB o más.
- Pendrive USB de 16 GB o más.
- La ISO verificada: `stone-os-0.1.0-rc1-x86_64.iso` (GITHUB_SETUP.md, pasos 9 y 10).

## 1. BIOS

BIOS **P3.00 CHIPSETMENU** y sus ajustes, con la CMOS borrada después de flashear: [docs/FIRMWARE.md](docs/FIRMWARE.md). Lo esencial (tecla **Supr** al encender):

| Ajuste | Valor |
| --- | --- |
| Integrated Graphics Controller | Forced |
| UMA Mode / Frame Buffer Size | UMA_SPECIFIED / 512MB |
| IOMMU | Disabled |
| Boot Mode | UEFI |

Guardar con **F10**.

## 2. Grabar la ISO en el pendrive (Mac)

**Con una aplicación (recomendado):** [Fedora Media Writer](https://fedoraproject.org/workstation/download) o [balenaEtcher](https://etcher.balena.io). Elige «imagen personalizada» / «Flash from file», la ISO, el pendrive, y graba. Al terminar, macOS puede decir que no puede leer el disco: es normal, pulsa **Expulsar**.

**Con la Terminal:**

```
diskutil list                         # identifica el pendrive, por ejemplo /dev/disk4
diskutil unmountDisk /dev/disk4
sudo dd if=stone-os-0.1.0-rc1-x86_64.iso of=/dev/rdisk4 bs=4m status=progress
diskutil eject /dev/disk4
```

Comprueba dos veces el número de disco: `dd` sobre el disco equivocado lo borra.

## 3. Arrancar el instalador

1. Pendrive en la BC-250, encender, entrar en la BIOS (**Supr**) → **Boot** → poner el pendrive (UEFI) primero, o usar **Boot Override** con él. Guardar y salir.
2. Si el instalador no muestra imagen: en el menú del pendrive, edita la entrada (tecla **e**), añade `nomodeset` al final de la línea `linux`, y arranca con **Ctrl+X**. Sólo para instalar.

## 4. Instalar

El instalador es Anaconda, con el teclado:

1. **Idioma** → Español.
2. **Destino de la instalación** → el SSD interno → automático (todo el disco).
3. **Red** → el cable aparece conectado.
4. **Usuario** → crea tu usuario y contraseña. Game Mode inicia sesión con él automáticamente; la contraseña sólo hace falta en modo escritorio o por SSH.
5. **Zona horaria** → la tuya.
6. **Comenzar instalación**. Cuando termine, **Reiniciar** y quita el pendrive.

El sistema instalado queda apuntando al canal de esta versión (`ghcr.io/<tu-usuario>/stone-os:rc`) para recibir actualizaciones.

## 5. Primer arranque

```
encender → pantalla negra con la línea blanca (Plymouth)
         → Steam Game Mode: idioma, red e inicio de sesión de Steam (primera vez)
         → STONE MACHINE en BALANCED; stoned comprueba la placa y el regulador
```

1. Una pantalla negra de 30–60 s en el primer arranque está reportada como normal en la BC-250.
2. Completa la configuración de Steam con el mando (o el teclado) e inicia sesión.
3. **Reinicia una vez** (botón STEAM → Energía → Reiniciar). STONE se registra como aplicación de Steam en el arranque siguiente a tu primer inicio de sesión, porque Steam reescribe esa lista al cerrarse y STONE no la toca mientras Steam está abierto.
4. Desde ese arranque, la línea de Plymouth continúa en el vídeo de arranque de STONE (que reemplaza la animación de Steam) y Steam abre **STONE** apenas su interfaz está lista. Ya no hace falta teclado.

Nunca se aplica un perfil CUSTOM ni nada por encima de BALANCED en el primer arranque. Si STONE no detecta una BC-250 (ID PCI `1002:13fe`) o el regulador de GPU, el ajuste de rendimiento queda desactivado y la GPU sigue con su configuración de fábrica.

## 6. Comprobar

Con una terminal (botón STEAM → Energía → Cambiar a escritorio, o SSH):

```
cat /usr/lib/stone-release
stonectl status
stone-home-session status
```

Después, sigue [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) y anota los resultados.

## Instalar sobre un Bazzite que ya tengas

Si la placa ya tiene Bazzite (variante Game Mode / deck), puedes cambiar a STONE OS sin reinstalar:

```
sudo bootc switch ghcr.io/<tu-usuario>/stone-os:rc
systemctl reboot
```

Para volver: `sudo bootc rollback` y reiniciar.
