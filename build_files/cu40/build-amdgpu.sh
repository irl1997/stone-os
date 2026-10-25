#!/usr/bin/bash
# STONE: builder stage of the Containerfile. Builds, for the exact kernel of the Bazzite base:
#   * amdgpu.ko with the BC-250 40 CU unlock (off unless amdgpu.bc250_cc_write_mode=3),
#   * stone-cu-verify, the GPU compute correctness test used before 40 CU is kept.
#
# Input : /usr/src/kernels/<KVER>    kernel-devel copied from the base image (same digest)
#         /ctx/cu40/                 this folder
# Output: /out/status                "ok" or "unavailable: <reason>"
#         /out/amdgpu.ko /out/kver /out/source   (only when status is ok)
#         /out/stone-cu-verify /out/stone-cu-verify.spv  (only when they built)
#
# It never fails the image build. If the kernel source tag is missing, the change no longer
# fits the code or the module does not match the kernel, /out/status says why and the image is
# built with Bazzite's own amdgpu (24 CU, as now); `stone-cu40 status` shows it.
set -uo pipefail
shopt -s nullglob

OUT=/out
WORK=/build
OGC_REPO="${OGC_REPO:-https://github.com/OpenGamingCollective/linux.git}"
mkdir -p "$OUT"

unavailable() { echo "unavailable: $*" > "$OUT/status"; echo "STONE 40 CU: NOT AVAILABLE in this image: $*" >&2; exit 0; }

# ---- the compute test first: it does not depend on the kernel ----------------------------
if glslangValidator -V /ctx/cu40/verify/stone-cu-verify.comp -o "$OUT/stone-cu-verify.spv" \
        && gcc -std=c11 -O2 -Wall -Wextra -Werror -o "$OUT/stone-cu-verify" /ctx/cu40/verify/stone-cu-verify.c -lvulkan -lm; then
    echo "stone-cu-verify built"
else
    rm -f "$OUT/stone-cu-verify" "$OUT/stone-cu-verify.spv"
    echo "WARNING: stone-cu-verify did not build; the 40 CU test will be unavailable" >&2
fi

# ---- 1. kernel of the base image ----------------------------------------------------------
kdirs=(/usr/src/kernels/*/)
[[ ${#kdirs[@]} -eq 1 ]] || unavailable "expected one kernel-devel in the base image, found ${#kdirs[@]}"
KDIR="${kdirs[0]%/}"
KVER="$(basename "$KDIR")"
[[ -f "$KDIR/Module.symvers" ]] || unavailable "kernel-devel of $KVER has no Module.symvers"
# 7.2.9-ogc3.1.fc44.x86_64 -> v7.2.9-ogc3 (tag of OpenGamingCollective/linux, Bazzite's kernel)
[[ "$KVER" =~ ^([0-9]+\.[0-9]+\.[0-9]+)-ogc([0-9]+)\.[0-9]+\.fc[0-9]+\.x86_64$ ]] \
    || unavailable "kernel $KVER is not an OGC kernel"
TAG="v${BASH_REMATCH[1]}-ogc${BASH_REMATCH[2]}"
echo "kernel $KVER -> source $OGC_REPO @ $TAG"

# ---- 2. amdgpu source of that exact kernel --------------------------------------------------
rm -rf "$WORK"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" --filter=blob:none --sparse \
    "$OGC_REPO" "$WORK/src" || unavailable "tag $TAG not found in $OGC_REPO"
git -C "$WORK/src" sparse-checkout set drivers/gpu/drm/amd || unavailable "sparse checkout failed"
mk() { sed -nE "s/^$1 = ([0-9]+)$/\1/p" "$WORK/src/Makefile"; }
src_ver="$(mk VERSION).$(mk PATCHLEVEL).$(mk SUBLEVEL)"
[[ "$KVER" == "$src_ver"-* ]] || unavailable "source $src_ver does not match kernel $KVER"
commit="$(git -C "$WORK/src" rev-parse HEAD)"

# ---- 3. the change ----------------------------------------------------------------------------
python3 /ctx/cu40/patch-gfx10.py "$WORK/src/drivers/gpu/drm/amd/amdgpu/gfx_v10_0.c" \
    || unavailable "the 40 CU change does not fit gfx_v10_0.c of $TAG"

# ---- 4. build against kernel-devel ---------------------------------------------------------------
# amdgpu_trace.h is included through a path relative to the kernel tree, so the module is built
# inside a copy of kernel-devel with amd/ in its place ($KDIR itself is not touched).
BK="$WORK/kernel"
mkdir -p "$BK" && cp -a "$KDIR/." "$BK/"
rm -rf "$BK/drivers/gpu/drm/amd" && mv "$WORK/src/drivers/gpu/drm/amd" "$BK/drivers/gpu/drm/amd"
M="$BK/drivers/gpu/drm/amd/amdgpu"
make -C "$BK" M="$M" KCFLAGS="-g0" -j"$(nproc)" modules > "$WORK/make.log" 2>&1 \
    || { tail -40 "$WORK/make.log" >&2; unavailable "amdgpu did not compile for $KVER"; }
ko="$M/amdgpu.ko"
[[ -f "$ko" ]] || unavailable "amdgpu.ko was not produced"
strip --strip-debug "$ko"

# ---- 5. checks ------------------------------------------------------------------------------------
vermagic="$(modinfo -F vermagic "$ko")"
[[ "$vermagic" == "$KVER "* ]] || unavailable "module vermagic '$vermagic' is not $KVER"
parms="$(modinfo -F parm "$ko")"
grep -q '^bc250_cc_write_mode:' <<<"$parms" || unavailable "module has no bc250_cc_write_mode"

install -m644 "$ko" "$OUT/amdgpu.ko"
echo "$KVER" > "$OUT/kver"
echo "$OGC_REPO $TAG $commit" > "$OUT/source"
echo ok > "$OUT/status"
echo "STONE 40 CU: amdgpu built for $KVER from $TAG ($commit), $(du -h "$OUT/amdgpu.ko" | cut -f1)"
