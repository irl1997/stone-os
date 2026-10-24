# Firmware de STONE MACHINE (AMD BC-250)

Se aplica una vez por unidad, al ensamblar, antes de instalar STONE OS. Fuente principal: [BIOS Flashing Guide — AMD BC250 Documentation](https://elektricm.github.io/amd-bc250-docs/bios/flashing/). STONE OS nunca flashea la BIOS: esto es un paso de fábrica, no una función del sistema.

## Archivo de BIOS de STONE MACHINE

| Archivo | Versión | SHA256 |
| --- | --- | --- |
| `BC250_3.00_CHIPSETMENU.ROM` | P3.00 modificada (menú de chipset y VRAM desbloqueados) | `48fbe5d366e6a56e2fdffdca848426216ba1f083610dab63db89d2f4e6c940b5` |

Es la versión que la documentación da como estándar comunitario y la más probada. Fuentes que publican ese mismo archivo: TuxThePenguin0 (GitLab), forgenam, tipitochen, csabakecskemeti, scrakcho y dannybastos.

**Antes de flashear, siempre:**

```
sha256sum BC250_3.00_CHIPSETMENU.ROM
```

Si el resultado no coincide exactamente con el de la tabla, no se flashea.

**Descartadas para STONE MACHINE**

| Versión | Motivo |
| --- | --- |
| P3.00 MeiMeiDXE (desbloqueo a 8 núcleos) | Reescribe el bloque de arranque: la opción de mayor riesgo. Además, el desbloqueo depende de cada placa |
| `P5.00_clv` | Expone ajustes de depuración críticos, fácil de inutilizar la placa y sin hash publicado |
| P4.00 / P5.00 de fábrica | P4.00 reportada inestable; la configuración de STONE se basa en P3.00 |

## Equipo necesario antes de empezar

- [ ] Programador **CH347** (3,3 V nativos). La guía recomienda tenerlo antes de empezar: es la única forma de recuperar una placa que no arranca. No usar CH341A de placa negra sin confirmar que trabaja a 3,3 V (muchos dan 5 V).
- [ ] Pendrive USB en FAT32.
- [ ] Fuente de 12 V comprobada, con la polaridad del conector de 8 pines verificada **antes de encender**: invertirla puede dañar la placa de forma permanente.

## Chips de la placa

| Chip | Tamaño | Acción |
| --- | --- | --- |
| `BIOS_A1` (Winbond W25Q128JVSQ o Macronix MX25L12835F) | 16 MB | Es el que se flashea |
| `SIO1_R` (Macronix pequeño) | 512 KB | **No tocar**: controla ventiladores y sensores |

## Procedimiento de STONE

1. **Copia de seguridad con el programador CH347**, dos lecturas idénticas:
   ```
   sudo flashrom -p ch347_spi -r backup_stock.bin
   sudo flashrom -p ch347_spi -r backup_verify.bin
   diff backup_stock.bin backup_verify.bin
   ```
   Si `diff` no muestra nada, las lecturas coinciden. Guardar el archivo fuera de la placa, etiquetado con el número de serie de la unidad.
2. **Flasheo por USB (EFI Shell)**, el método que la guía recomienda para placas que funcionan. Desconectar todos los discos. En el pendrive: `AfuEfix64.efi`, `Flash.nsh`, `amdvbflash.efi`, la carpeta `EFI` y el archivo de BIOS con el nombre que espera `Flash.nsh`. En la consola EFI:
   ```
   blk0:
   Flash.nsh
   ```
   No apagar durante el proceso. Si parece colgado, esperar al menos 15 minutos.
   **Atención:** la guía indica renombrar el archivo modificado como `Robin5.00`, el mismo nombre que la P5.00 de fábrica. Comprobar el hash del archivo que está en el pendrive justo antes de flashear.
3. **Alternativa con programador**, si el método USB falla:
   ```
   sudo flashrom -p ch347_spi -w BC250_3.00_CHIPSETMENU.ROM
   ```
4. **Borrar la CMOS**: quitar la pila CR2032 al menos 60 s (o puente CMOS 20 s). Comprobar que el reloj aparece desajustado al arrancar; si no, repetir.

El flasheo interno desde Linux (`flashrom -p internal`) existe y está probado en Bazzite, pero reescribe el chip del que arranca la propia placa. No se usa en el ensamblaje de STONE MACHINE.

## Ajustes de BIOS (tecla Supr al encender)

| Menú | Ajuste | Valor |
| --- | --- | --- |
| Chipset → GFX Configuration | Integrated Graphics Controller | Forced |
| Chipset → GFX Configuration | UMA Mode | UMA_SPECIFIED |
| Chipset → GFX Configuration | UMA Frame Buffer Size | 512MB |
| Advanced → CPU Configuration | IOMMU | Disabled |
| Boot | Boot Mode | UEFI |
| Ventilador (CPU_FAN1) | Fan Control | Customize, curva de STONE [PENDIENTE: valores] |

Guardar con **F10**. No cambiar voltajes, tiempos ni otras opciones que la BIOS modificada expone: la guía advierte que pueden dañar la placa.

## Comprobación desde Linux

```
sudo dmidecode -t bios
lspci | grep VGA
grep -i mem /proc/meminfo
```

`dmidecode` debe mostrar la versión modificada.

## Recuperación

- Si Linux todavía arranca, se puede restaurar la copia con el programador.
- Si la placa no arranca: revisar alimentación, repetir el borrado de CMOS y probar otro adaptador de vídeo antes de darla por inutilizada. Después, flashear un archivo verificado con el CH347.

## Alimentación

- Sólo 12 V: PCIe de 8 pines (más los dos Micro-Fit). No usar adaptadores SATA ni de 6 a 8 pines.
- Fuente con un único raíl de 12 V de al menos 20 A (300 W o más).
- La placa no enciende una fuente ATX por sí sola: hace falta puente o interruptor de PS_ON.

## Instalación del sistema

- Si la instalación no muestra imagen, arrancar con `nomodeset` y retirarlo después.
- Una pantalla negra de 30–60 s en el primer arranque está reportada como normal. [PENDIENTE: medir en STONE OS]
