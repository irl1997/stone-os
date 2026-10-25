# STONE OS — image derived from Bazzite (Steam Game Mode variant) for STONE MACHINE (AMD BC-250).
# Layout and build steps follow ublue-os/image-template: one Containerfile, scripts run from a
# build context that is not copied into the final image, `bootc container lint` at the end.
#
# The base reference is passed by the build (scripts/build-local.sh or the build-image workflow)
# pinned to a digest, so the same commit builds on the same Bazzite. Default for a bare
# `podman build`: the current stable Bazzite on Fedora 44.
ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-deck:stable-44

FROM scratch AS ctx
COPY build_files /
COPY system_files /system_files
COPY VERSION /VERSION

FROM ${BASE_IMAGE} AS base

## BC-250 40 CU option: amdgpu rebuilt for the base image's exact kernel, with the unlock
## present but off (build_files/cu40/build-amdgpu.sh). Never fails the build: when it cannot be
## made, the image keeps Bazzite's own amdgpu and says so.
FROM registry.fedoraproject.org/fedora:44 AS cu40
RUN dnf -y install --setopt=install_weak_deps=False \
        gcc make flex bison bc perl python3 diffutils findutils elfutils-libelf-devel \
        openssl openssl-devel dwarves git-core kmod binutils xz \
        glslang vulkan-loader-devel vulkan-headers \
    && dnf clean all
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=bind,from=base,source=/usr,target=/base-usr \
    mkdir -p /usr/src/kernels && cp -a /base-usr/src/kernels/. /usr/src/kernels/ 2>/dev/null; \
    bash /ctx/cu40/build-amdgpu.sh

FROM base

ARG BASE_IMAGE
ARG BASE_DIGEST=""
ARG STONE_COMMIT="unknown"
ARG STONE_BUILD_DATE="unknown"
ARG STONE_CHANNEL="dev"

### MODIFICATIONS
## Every system change is made here, by build.sh; never by hand on an installed console.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=bind,from=cu40,source=/out,target=/cu40 \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    BASE_IMAGE="${BASE_IMAGE}" BASE_DIGEST="${BASE_DIGEST}" STONE_COMMIT="${STONE_COMMIT}" \
    STONE_BUILD_DATE="${STONE_BUILD_DATE}" STONE_CHANNEL="${STONE_CHANNEL}" \
    /ctx/build.sh

LABEL org.opencontainers.image.title="stone-os" \
      org.opencontainers.image.description="STONE OS: the system of STONE MACHINE, built on Bazzite for the AMD BC-250" \
      org.opencontainers.image.revision="${STONE_COMMIT}" \
      org.opencontainers.image.created="${STONE_BUILD_DATE}" \
      org.opencontainers.image.base.name="${BASE_IMAGE}" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}" \
      org.opencontainers.image.licenses="Apache-2.0"

### LINTING
## Verify the final image is a valid bootc system.
RUN bootc container lint
