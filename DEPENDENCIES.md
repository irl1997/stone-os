# Dependencias de STONE OS 0.1.0-rc7

Las versiones exactas de cada imagen construida quedan escritas en `/usr/lib/stone-release` y en `build-metadata.json` (artefacto del build y archivo de cada release). Esta tabla dice de dónde sale cada pieza y cómo se actualiza.

## En la imagen

| Paquete | Versión | Origen | Para qué | Cómo se actualiza |
| --- | --- | --- | --- | --- |
| Bazzite `bazzite-deck` | `stable-44`, fijada por digest en cada build | `ghcr.io/ublue-os/bazzite-deck` | Base: Fedora Atomic 44, kernel, Mesa, Steam, Gamescope, Game Mode, actualizaciones | Cada build resuelve el digest actual de `stable-44`. Para fijar uno: `BASE_DIGEST` en `config/build.env`. Cambio de Fedora (45): cambiar `BASE_TAG` y pasar CI + HARDWARE_VALIDATION |
| Kernel | El de la base (registrado como `STONE_KERNEL`) | Bazzite | Driver AMDGPU de la BC-250 | Con la base. `check-kernel.sh` hace fallar el build si cae en 6.15.0–6.15.6 o 6.17.8–6.17.10 |
| Mesa (RADV) | El de la base (`STONE_MESA`) | Bazzite | Vulkan de la GPU gfx1013 | Con la base |
| Gamescope | El de la base (`STONE_GAMESCOPE`) | Bazzite | Compositor de Game Mode | Con la base |
| `cyan-skillfish-governor-smu` | Última del COPR (`STONE_GOVERNOR_VERSION`); fijable con `GOVERNOR_VERSION` | COPR `filippor/bazzite` (indicado por la guía de Bazzite para la BC-250) | Regulador de frecuencia de la GPU; su D-Bus es la única vía de `stoned` al hardware | Se reinstala en cada build. **Cuando una versión pase HARDWARE_VALIDATION, fijarla** (`GOVERNOR_VERSION`). `governor-config.py` hace fallar el build si la nueva versión no trae D-Bus o sus valores salen del rango estable |
| `vkmark` | La de Fedora 44 | Repositorios de Fedora | Carga de la prueba de estabilidad | Con cada build. Si falta, el build avisa y STONE TUNING no ofrece pruebas |
| `python3-gobject` | La de Fedora 44 | Repositorios de Fedora | `stoned` y STONE Home (GLib, Gio, GTK, WebKit) | Con cada build; el build falla si no se puede importar |
| `webkitgtk6.0`, `gtk4` | Las de Fedora 44 (`STONE_WEBKITGTK`) | Repositorios de Fedora | Ventana y motor de STONE Home | Con cada build; el build falla si no se pueden importar |
| Plymouth | El de la base | Bazzite | Arranque con el tema `stone` | Con la base |
| `plymouth-plugin-script` | La de Fedora 44 | Repositorios de Fedora | Motor de los temas Plymouth de tipo script (el tema `stone` lo es); la base no lo trae | Con cada build; el build falla si falta |
| Jost 5.3.0, Inter 5.3.0, IBM Plex Mono 5.3.0 (latin) | Fijas, en el repositorio | Paquetes npm `@fontsource/*` (SIL OFL 1.1) | Tipografía de STONE Home sin red | A mano: copiar los `.woff2` nuevos y sus licencias |
| Steam | El de la base | Bazzite | Lanzador, Game Mode, ajustes de sistema | Se actualiza solo (Steam) |

STONE no añade repositorios a la imagen final: el COPR se activa sólo durante el build y se desactiva después.

## Sólo para construir

| Herramienta | Versión | Dónde |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 `3d3c42e5…` | Todos los flujos |
| `ublue-os/remove-unwanted-software` | `695eb75b…` | Imagen e ISO |
| `sigstore/cosign-installer` | v4.1.2 `6f9f1778…` (cosign v3.1.2) | Firma opcional de la imagen |
| `osbuild/bootc-image-builder-action` | `8661cd38…` (la misma que fija `ublue-os/image-template`) | ISO |
| `quay.io/centos-bootc/bootc-image-builder` | `latest`, digest registrado en el resumen del flujo | ISO |
| `actions/upload-artifact` | v7.0.2 `cf430e03…` | Artefactos |
| `actions/download-artifact` | v8.0.1 `3e5f45b2…` | Release |
| `actions/setup-node` | v7.0.0 `82076278…` | CI |
| Runner | `ubuntu-26.04` (imagen, como la plantilla de Universal Blue); `ubuntu-24.04` (ISO: en 26.04 osbuild falla al montar por AppArmor; y CI) | GitHub |
| podman, skopeo, rpm-ostree (rechunk) | Los del runner / de la propia imagen | Imagen |

Actualización: Dependabot propone nuevas versiones de las acciones (por SHA) como pull requests que deben pasar CI.

## Sólo para probar

| Herramienta | Versión | Fijada en |
| --- | --- | --- |
| Playwright (Chromium) | 1.63.0 | `tests/e2e/package-lock.json` |
| ESLint, globals | 9.39.1, 16.5.0 | `tests/e2e/package-lock.json` |
| ruff | 0.16.9 | `ci.yml` |
| actionlint | 1.7.11 (`actionlint-py==1.7.11.24`) | `ci.yml` |
| shellcheck, xmllint, systemd-analyze, Xvfb, WebKitGTK 6 | Paquetes de Ubuntu 24.04 | `ci.yml` |

Nada de esto entra en la imagen.
