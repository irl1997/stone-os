# development/

Herramientas de diseño y desarrollo. **Nada de esta carpeta entra en la imagen.**

| Archivo | Qué es |
| --- | --- |
| `stone-os-prototipo.html` | Prototipo navegable de STONE OS: referencia de diseño y QA. Simula `stoned` y el regulador (SimStoned), con barra de depuración, inyección de fallos y cuelgue simulado. Se abre en cualquier navegador |
| `build_home.py` | Genera la página de producción (`system_files/usr/share/stone-home/index.html`) a partir del prototipo, sustituyendo sólo la capa simulada por las piezas de `home_parts/`. `--check` comprueba que está al día |
| `home_parts/` | Piezas de producción de la página: transporte al host, biblioteca de Steam, juego, energía, arranque, autotest |
| `stone_home_devserver.py` | STONE Home en un navegador normal, con el host real por HTTP (127.0.0.1). Usado por la prueba end to end |
| `make_boot_video.mjs` | Genera el vídeo de arranque de Steam de STONE (`boot/stone-startup.webm`), fotograma a fotograma desde la posición de la línea de Plymouth. Necesita `ffmpeg` con VP9 |
| `make_assets.mjs` | Dibuja el icono y las carátulas de Steam del acceso directo «STONE» con las fuentes incluidas |

Flujo para cambiar la interfaz: editar el prototipo (y, si toca la parte real, `home_parts/`) → `python3 development/build_home.py` → `scripts/test-all.sh`.
