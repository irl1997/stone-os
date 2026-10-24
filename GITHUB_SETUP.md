# GitHub: de esta carpeta al instalador de STONE OS

Guía para hacerlo una vez, desde un Mac, sin experiencia con GitHub Actions y sin editar código. Al final tendrás la imagen de STONE OS publicada y un instalador ISO verificado.

```
esta carpeta → repositorio en GitHub → GitHub Actions construye → imagen en GHCR → ISO en Releases → BC-250
```

Tiempo aproximado: 20 minutos tuyos y 1–3 horas de GitHub trabajando solo.

> **Estado honesto:** los flujos de GitHub Actions de este repositorio están creados y revisados, pero **todavía no se han ejecutado nunca en GitHub**. La primera ejecución será la tuya. Si algún paso falla, el registro del paso dice por qué (ver «Si algo falla» al final).

---

## 1. Crear el repositorio

1. Entra en [github.com](https://github.com) con tu cuenta (o crea una).
2. Arriba a la derecha, **+** → **New repository**.
3. **Repository name:** `stone-os`.
4. Marca **Public**. Hace falta que sea público para que:
   - GitHub Actions sea gratuito sin límite de minutos,
   - la consola pueda descargar las actualizaciones sin contraseña.
5. **No** marques «Add a README», ni .gitignore, ni licencia: ya vienen en esta carpeta.
6. **Create repository**.

## 2. Subir los archivos

La carpeta tiene archivos ocultos (`.github`, `.gitignore`) que el Finder no muestra y que **sí** hay que subir: sin `.github` no hay GitHub Actions. Por eso no conviene arrastrar archivos a la web. Elige una de estas dos formas.

### Opción A — GitHub Desktop (sin terminal, recomendada)

1. Descarga e instala [GitHub Desktop](https://desktop.github.com) e inicia sesión con tu cuenta.
2. Descomprime `stone-os-0.1.0-rc1-source.zip`. Queda una carpeta `stone-os`.
3. GitHub Desktop → **File → Add Local Repository…** → elige la carpeta `stone-os`.
4. Te dirá que no es un repositorio de Git: pulsa **create a repository**. Deja el nombre `stone-os` y **Create Repository**.
5. Abajo a la izquierda, en **Summary**, escribe `STONE OS 0.1.0-rc1` y pulsa **Commit to main**.
6. Arriba, **Publish repository**. **Desmarca** «Keep this code private» y confirma. Si dice que ya existe, usa **Repository → Repository settings → Remote** y pega `https://github.com/<tu-usuario>/stone-os.git`, luego **Push origin**.

### Opción B — Terminal

```
cd ~/Downloads                      # donde esté el zip
unzip stone-os-0.1.0-rc1-source.zip
cd stone-os
git init -b main
git add .
git commit -m "STONE OS 0.1.0-rc1"
git remote add origin https://github.com/<tu-usuario>/stone-os.git
git push -u origin main
```

Cambia `<tu-usuario>` por tu usuario de GitHub. Si el Mac pide instalar las herramientas de desarrollo (para `git`), acepta. Si `git push` pide contraseña, usa un *token* de GitHub (Settings → Developer settings → Personal access tokens) o la Opción A.

## 3. Revisar Actions

1. En tu repositorio en GitHub, pestaña **Actions**. Si aparece un aviso para activar los flujos, pulsa **I understand my workflows, go ahead and enable them**.
2. **Settings → Actions → General**: deja «Allow all actions and reusable workflows». En «Workflow permissions» puede quedar «Read repository contents»: cada flujo declara exactamente los permisos que necesita.

No hace falta crear ningún token: los flujos usan el `GITHUB_TOKEN` que GitHub da a cada ejecución.

## 4. Secretos (opcional)

Ninguno es obligatorio. Uno es recomendable si vas a compartir la imagen:

**`SIGNING_SECRET`** — firma la imagen con cosign para que se pueda comprobar que salió de tu repositorio. Sin él la imagen se publica igual, sin firma, y el resumen del build lo dice.

```
brew install cosign
cosign generate-key-pair          # deja la contraseña vacía (pulsa Enter dos veces)
```

Crea `cosign.key` (privada) y `cosign.pub` (pública).

1. GitHub → **Settings → Secrets and variables → Actions → New repository secret**. Nombre `SIGNING_SECRET`, valor: el contenido completo de `cosign.key`.
2. Copia `cosign.pub` a la carpeta del repositorio, haz commit y push.
3. **Nunca subas `cosign.key`** (el `.gitignore` ya lo excluye). Guárdala fuera del repositorio.

## 5. Primera ejecución: CI e imagen de desarrollo

El push del paso 2 ya arrancó dos flujos. En **Actions**:

| Flujo | Qué hace | Tiempo |
| --- | --- | --- |
| **CI** | Valida la estructura y ejecuta todas las pruebas | ~10 min |
| **Build image** | Vuelve a pasar CI y construye la imagen; la publica como `ghcr.io/<tu-usuario>/stone-os:dev` | 30–60 min |

Cada uno debe terminar con ✓ verde. Si CI falla, la imagen no se construye.

## 6. Hacer pública la imagen (una sola vez)

GitHub crea los paquetes como privados. Hay que hacerlo público para que el instalador y la consola lo descarguen:

1. Tu perfil → pestaña **Packages** → **stone-os**.
2. **Package settings** (abajo a la derecha) → **Change visibility** → **Public** → escribe `stone-os` para confirmar.

## 7. Publicar la versión 0.1.0-rc1 (imagen + instalador)

Una versión se publica creando la etiqueta `v0.1.0-rc1` (la misma que el archivo `VERSION`, con una `v` delante).

**GitHub Desktop:** pestaña **History** → clic derecho en el último commit → **Create Tag…** → `v0.1.0-rc1` → **Create Tag** → **Push origin** (empuja también la etiqueta).

**Terminal:**

```
git tag v0.1.0-rc1
git push origin v0.1.0-rc1
```

Eso arranca el flujo **Release**:

```
Tag matches VERSION → CI → Image (GHCR: 0.1.0-rc1, rc) → Installer (ISO) → GitHub Release
```

Tarda 1–3 horas. Si la etiqueta no coincide con `VERSION`, se detiene en el primer paso sin publicar nada.

## 8. Encontrar la imagen

Tu perfil → **Packages** → **stone-os**. Verás las etiquetas `0.1.0-rc1` y `rc` (y `dev` de los builds de `main`). La dirección es:

```
ghcr.io/<tu-usuario>/stone-os:0.1.0-rc1
```

## 9. Descargar el instalador

Tu repositorio → **Releases** (columna derecha) → **STONE OS 0.1.0-rc1**. Descarga:

- todos los archivos `stone-os-0.1.0-rc1-x86_64.iso.part-00`, `.part-01`, …
- `SHA256SUMS`

GitHub limita cada archivo de una release a 2 GiB, por eso la ISO viene en partes. Ponlos todos en la misma carpeta (por ejemplo Descargas).

> Las mismas partes están también como artefacto del flujo (Actions → la ejecución de Release → **Artifacts**) durante 30 días.

## 10. Unir y verificar

En la Terminal del Mac, en la carpeta de las descargas:

```
cd ~/Downloads
cat stone-os-0.1.0-rc1-x86_64.iso.part-* > stone-os-0.1.0-rc1-x86_64.iso
shasum -a 256 -c SHA256SUMS --ignore-missing
```

Debe decir `OK` para la ISO y para cada parte. **Si alguna dice `FAILED`, no la uses**: vuelve a descargar esa parte.

## 11. Instalar en la BC-250

Sigue **[INSTALL.md](INSTALL.md)**: BIOS, pendrive, instalador, primer arranque.

---

## Actualizaciones futuras

- Cambios en `main` → imagen `dev` (pruebas).
- Nueva versión: edita `VERSION` (por ejemplo `0.1.0-rc2`), commit, push, etiqueta `v0.1.0-rc2`. Las consolas instaladas desde una RC siguen el canal `rc` y la reciben como actualización.

## Si algo falla

1. **Actions** → la ejecución en rojo → el paso en rojo. El registro dice el motivo.
2. Errores frecuentes:

| Mensaje | Qué hacer |
| --- | --- |
| `tag v… does not match VERSION …` | La etiqueta y el archivo `VERSION` no coinciden. Borra la etiqueta y créala bien |
| La consola instalada no encuentra actualizaciones (`denied` / `unauthorized`) | La imagen sigue privada: paso 6 |
| `No space left on device` | Repite la ejecución (**Re-run jobs**); si persiste, abre un issue con el registro |
| Falla `bootc container lint` o `build.sh` | El registro indica qué comprobación de STONE falló (por ejemplo un paquete que Bazzite cambió). No se publicó nada |
| Falla el paso del regulador (`cyan-skillfish-governor-smu`) | El COPR cambió o no responde. Repite más tarde; si sigue, ver DEPENDENCIES.md |

Nada de lo que falla en GitHub llega a la consola: sólo se publica lo que pasó todos los pasos.
