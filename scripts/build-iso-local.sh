#!/usr/bin/env bash
# Build the STONE OS installer ISO locally (Linux, rootful podman), like build-installer.yml.
#   scripts/build-iso-local.sh <image-ref-to-install> [<update-ref>]
# Example: scripts/build-iso-local.sh ghcr.io/<owner>/stone-os:0.1.0-rc1 ghcr.io/<owner>/stone-os:rc
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=config/build.env
source config/build.env
image="${1:?image reference to install}"; update="${2:-$1}"
out="$(pwd)/output"; mkdir -p "${out}"
cfg="$(mktemp)"; trap 'rm -f "${cfg}"' EXIT
sed -e "s|@UPDATE_REF@|${update}|g" -e "s|@IMAGE_REF@|${image}|g" disk_config/iso.toml > "${cfg}"
sudo podman pull "${image}"
sudo podman run --rm -it --privileged --pull=newer --net=host \
    --security-opt label=type:unconfined_t \
    -v "${cfg}:/config.toml:ro" -v "${out}:/output" \
    -v /var/lib/containers/storage:/var/lib/containers/storage \
    "${BIB_IMAGE}" --type anaconda-iso --rootfs btrfs --use-librepo=True "${image}"
iso="$(find "${out}" -name '*.iso' | head -n1)"
version="$(tr -d '[:space:]' < VERSION)"
mv "${iso}" "${out}/stone-os-${version}-x86_64.iso"
(cd "${out}" && sha256sum "stone-os-${version}-x86_64.iso" > SHA256SUMS)
echo "ISO: ${out}/stone-os-${version}-x86_64.iso"
