#!/usr/bin/bash
# Detiene el build si el kernel de la imagen está en un rango con fallos conocidos en la BC-250.
# Rangos según la documentación comunitaria (elektricM/amd-bc250-docs):
#   6.15.0 – 6.15.6   y   6.17.8 – 6.17.10
set -euo pipefail

count="$(find /usr/lib/modules -mindepth 1 -maxdepth 1 | wc -l)"
if [ "${count}" -ne 1 ]; then
    echo "ERROR: se esperaba un único kernel en /usr/lib/modules y hay ${count}." >&2
    exit 1
fi

kver="$(find /usr/lib/modules -mindepth 1 -maxdepth 1 -printf '%f')"
base="${kver%%-*}"                  # p. ej. 6.17.11
IFS=. read -r major minor patch <<< "${base}"
patch="${patch:-0}"

broken=0
if [ "${major}" -eq 6 ] && [ "${minor}" -eq 15 ] && [ "${patch}" -le 6 ]; then broken=1; fi
if [ "${major}" -eq 6 ] && [ "${minor}" -eq 17 ] && [ "${patch}" -ge 8 ] && [ "${patch}" -le 10 ]; then broken=1; fi

if [ "${broken}" -eq 1 ]; then
    echo "ERROR: kernel ${kver} está en un rango con fallos de GPU conocidos en la BC-250." >&2
    exit 1
fi

echo "Kernel ${kver}: fuera de los rangos con fallos conocidos."
