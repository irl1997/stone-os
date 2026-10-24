# Construir STONE OS

Dos caminos que producen lo mismo:

| | GitHub Actions (principal) | Local (Linux) |
| --- | --- | --- |
| Imagen | `.github/workflows/build-image.yml` | `scripts/build-local.sh` |
| Instalador ISO | `.github/workflows/build-installer.yml` | `scripts/build-iso-local.sh` |
| Pruebas | `.github/workflows/ci.yml` | `scripts/validate.sh` y `scripts/test-all.sh` |

En un Mac se usa GitHub (las imágenes bootc se construyen con podman en Linux). La guía paso a paso está en [GITHUB_SETUP.md](GITHUB_SETUP.md).

## Arquitectura de build

Método recomendado por Universal Blue para imágenes personalizadas de Bazzite (repositorio `ublue-os/image-template`, octubre de 2026): un `Containerfile` que parte de la imagen de Bazzite y ejecuta `build_files/build.sh`, `bootc container lint` al final, imagen OCI/bootc publicada en GHCR, ISO con bootc-image-builder (`anaconda-iso`). STONE OS sigue exactamente ese esquema; no usa BlueBuild ni otra capa.

```
VERSION ─┐
config/build.env ─┤      podman build (Containerfile → build_files/build.sh)
build_files/ ─────┤  ──► bootc container lint
system_files/ ────┘      rpm-ostree rechunk ──► ghcr.io/<owner>/stone-os:<tags> ──► bootc-image-builder ──► ISO
```

### Origen único de la versión

`VERSION` (por ejemplo `0.1.0-rc1`). El build la escribe en `/usr/lib/stone-release` y en la etiqueta `org.opencontainers.image.version`; `scripts/release-info.sh` deriva de ella el canal y las etiquetas. Nada más en el repositorio contiene un número de versión.

### Canales y etiquetas

| Origen | Canal | Etiquetas de la imagen |
| --- | --- | --- |
| push a `main` | dev | `dev`, `dev-<sha7>` |
| etiqueta `v0.1.0-rc1` | rc | `0.1.0-rc1`, `rc` |
| etiqueta `v0.2.0-beta1` | beta | `0.2.0-beta1`, `beta` |
| etiqueta `v1.0.0` | stable | `1.0.0`, `stable`, `latest` |

La ISO de una versión instala la etiqueta fija (`0.1.0-rc1`) y deja la consola siguiendo su canal (`rc`). `latest` sólo existe para versiones estables y nada del proceso depende de ella.

### Reproducibilidad

- **Base fijada:** el build resuelve `ghcr.io/ublue-os/bazzite-deck:stable-44` a un digest y construye `FROM` ese digest. Para reconstruir exactamente una imagen anterior, copia `STONE_BASE_DIGEST` de su `/usr/lib/stone-release` a `BASE_DIGEST` en `config/build.env`.
- **Dentro de la imagen** (`/usr/lib/stone-release`): versión, canal, commit, fecha, imagen y digest base, versión de Bazzite, kernel, Mesa, Gamescope, WebKitGTK, versión y configuración del regulador.
- **Etiquetas OCI:** `org.opencontainers.image.version`, `.revision`, `.created`, `.base.name`, `.base.digest`, `.source`.
- **`build-metadata.json`** (artefacto del flujo y archivo de la release): imagen, digest, base, etiquetas, commit y el contenido de `stone-release`.
- **Límites conocidos:** los paquetes que `build.sh` instala desde los repositorios de Fedora (WebKitGTK, vkmark) y el COPR del regulador se resuelven en el momento del build; sus versiones quedan registradas pero no fijadas (ver DEPENDENCIES.md). El constructor de la ISO (`bootc-image-builder:latest`) se registra por digest en el resumen del flujo.

## GitHub Actions

| Flujo | Cuándo | Permisos |
| --- | --- | --- |
| `ci.yml` | pull request, push a `main`, llamado por los demás | `contents: read` |
| `build-image.yml` | push a `main`, manual, llamado por `release.yml` | `contents: read`, `packages: write` |
| `build-installer.yml` | manual, llamado por `release.yml` | `contents: read`, `packages: read` |
| `release.yml` | etiqueta `v*` | `contents: write` (sólo el paso que crea la release) |

Todas las acciones están fijadas por SHA de commit (las mismas versiones que usa `ublue-os/image-template`). Dependabot propone actualizaciones como pull requests que deben pasar CI.

Secretos: ninguno obligatorio. `GITHUB_TOKEN` basta para publicar en GHCR y crear la release. `SIGNING_SECRET` (clave cosign) es opcional para firmar la imagen.

**Puerta de publicación:** la imagen sólo se construye si CI pasa (validación + todas las pruebas, sin pruebas omitidas: `STONE_REQUIRE_ALL=1`); el instalador sólo desde una imagen publicada; la release sólo si la etiqueta coincide con `VERSION` y las sumas SHA-256 se verifican antes de subir.

## Local, en Linux

Requisitos: `podman`, `skopeo`, `git`, `sudo` (el rechunk y el ISO necesitan podman con root), unos 60 GB libres.

```
git clone https://github.com/<owner>/stone-os.git
cd stone-os
scripts/validate.sh
PYTHON=python3 scripts/test-all.sh
scripts/build-local.sh --owner <owner>                 # → localhost/stone-os:0.1.0-rc1
```

`--no-rechunk` omite el rechunk (más rápido, actualizaciones más grandes).

Probar la imagen en una máquina virtual con Bazzite instalado:

```
podman save -o stone-os.tar localhost/stone-os:0.1.0-rc1          # en la máquina de build
# copia stone-os.tar a la VM, y en la VM:
sudo podman load -i stone-os.tar
sudo bootc switch --transport containers-storage localhost/stone-os:0.1.0-rc1
systemctl reboot
```

En una VM el regulador de GPU no arranca (no hay BC-250): es lo esperado, y STONE debe mostrarlo como «no disponible».

ISO local desde una imagen publicada:

```
scripts/build-iso-local.sh ghcr.io/<owner>/stone-os:0.1.0-rc1 ghcr.io/<owner>/stone-os:rc
# → output/stone-os-0.1.0-rc1-x86_64.iso y output/SHA256SUMS
```

## Pruebas

```
scripts/validate.sh                  # estructura, shell, Python, XML, TOML, unidades systemd, flujos, página de STONE Home
PYTHON=python3 scripts/test-all.sh   # todas las pruebas de software
```

Dependencias de las pruebas en Debian/Ubuntu:

```
sudo apt install python3-gi gir1.2-glib-2.0 gir1.2-gtk-4.0 gir1.2-webkit-6.0 dbus xvfb xauth shellcheck libxml2-utils
pip install ruff==0.16.9 actionlint-py==1.7.11.24
(cd tests/e2e && npm ci && npx playwright install --with-deps chromium)
```

| Suite | Qué prueba |
| --- | --- |
| `tests/run_tests.py` (67) | `stoned` contra un regulador D-Bus simulado: recuperación, última buena, Safe Mode, aplicación con lectura de vuelta, errores, telemetría, vigilancia térmica, partidas de Steam, comparación BEFORE/AFTER |
| `tests/test_home.py` (20) | Biblioteca de Steam, acceso directo de STONE, vídeo de arranque, apertura confirmada, tabla de peticiones del host, puente D-Bus contra un `stoned` real |
| `tests/webkit_selftest.py` | El shell real GTK 4 + WebKitGTK 6 contra un `stoned` real, bajo Xvfb |
| `tests/e2e_home.py` | La página de producción en Chromium con el mando simulado por teclado: inicio, Performance Center, perfil por juego, lanzar y cerrar juego, apagar; y lo que llegó al regulador, a Steam y a logind |
| `tests/e2e/lint-page.mjs` | ESLint sobre el JavaScript de la página |

Pasar todas significa **CODE TESTED**. Nada sustituye a HARDWARE_VALIDATION.md.

## La página de STONE Home

`system_files/usr/share/stone-home/index.html` se genera desde el prototipo de diseño (`development/stone-os-prototipo.html`) con `development/build_home.py`, que sustituye sólo la capa simulada (ver docs/STONE_HOME.md). Tras cambiar el prototipo o `development/home_parts/`:

```
python3 development/build_home.py
```

`scripts/validate.sh` falla si la página no está al día.
