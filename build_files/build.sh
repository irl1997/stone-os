#!/usr/bin/bash
# STONE OS — customisation of the Bazzite (bazzite-deck) image for STONE MACHINE.
# Runs inside the container build (Containerfile). Any error stops the build: an image that
# would boot without a critical STONE layer is never produced.
set -ouex pipefail

STONE_VERSION="$(tr -d '[:space:]' < /ctx/VERSION)"
[[ "${STONE_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]] || { echo "ERROR: VERSION '${STONE_VERSION}' is not X.Y.Z[-pre]"; exit 1; }

### 1. STONE system files (stoned, STONE Home, units, policies, Plymouth theme, no-sleep config)
cp -avf /ctx/system_files/. /

### 2. Kernel check
## BC-250 community documentation reports GPU driver failures on 6.15.0–6.15.6 and
## 6.17.8–6.17.10. If Bazzite ever ships one of those, the build fails instead of producing an
## image that does not run properly on the board.
/ctx/check-kernel.sh

### 3. BC-250 GPU governor
## Without it the BC-250 GPU stays at 1500 MHz; with it, it scales ~1000–2000 MHz with load.
## COPR repository named by Bazzite's BC-250 guide. Disabled again after installing so it does
## not stay enabled in the final image. GOVERNOR_VERSION empty = latest in the COPR; set it
## (build arg / environment) to pin a version validated on the board.
GOVERNOR_VERSION="${GOVERNOR_VERSION:-}"
dnf5 -y copr enable filippor/bazzite
dnf5 -y install "cyan-skillfish-governor-smu${GOVERNOR_VERSION:+-${GOVERNOR_VERSION}}"
dnf5 -y copr disable filippor/bazzite
systemctl enable cyan-skillfish-governor-smu.service
## The package's own configuration, with one change: its D-Bus interface (used by stoned)
## enabled. Voltage curve, range and thermal threshold stay as the package ships them. The build
## fails if the package has no D-Bus section or its values leave the stable range.
GOV_INFO="$(/ctx/governor-config.py /etc/cyan-skillfish-governor-smu/config.toml)"
echo "${GOV_INFO}"

### 4. No sleep, not even from Steam
## The BC-250 does not come back from s2idle. sleep.conf.d forbids it in systemd; Steam Game Mode
## has its own idle suspend, which ends up asking for the same targets. Masked, any suspend
## request is refused.
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

### 5. Handheld Daemon: not for a desktop console
## The BC-250 guide associates it with micro-stutter. Masked if the base has it.
for unit in hhd.service hhd@.service; do
    if [ -e "/usr/lib/systemd/system/${unit}" ]; then
        systemctl mask "${unit}"
    fi
done

### 6. Stability test workload of the Performance Center
## vkmark (Vulkan, open source). If it is missing the build goes on and says so; stoned then
## reports the stability test as unsupported (STONE TUNING shows it, nothing pretends).
dnf5 -y install --skip-unavailable vkmark
if ! command -v vkmark >/dev/null 2>&1; then
    echo "WARNING: vkmark not available; STONE TUNING will offer no stability test on this image."
fi

### 7. STONE System Service (stoned)
## Unprivileged service (user `stoned`, sysusers.d) talking to the governor over D-Bus.
dnf5 -y install --skip-unavailable python3-gobject
python3 -c "import gi; gi.require_version('Gio', '2.0'); from gi.repository import Gio" \
    || { echo "ERROR: python3-gobject missing; stoned cannot run"; exit 1; }
python3 -m compileall -q /usr/lib/stone/stoned
systemctl enable stoned-recover.service stoned.service

### 8. STONE Home (the console interface) inside Steam Game Mode
## GTK 4 + WebKitGTK 6 window started by Steam as the "STONE" shortcut; user units bound to the
## Game Mode session register the shortcut and open it (stone_home/shortcuts.py).
dnf5 -y install webkitgtk6.0 gtk4
python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('WebKit', '6.0'); from gi.repository import Gtk, WebKit" \
    || { echo "ERROR: WebKitGTK 6 / GTK 4 bindings missing; STONE Home cannot run"; exit 1; }
python3 -m compileall -q /usr/lib/stone/stone_home
test -x /usr/libexec/stone-home || { echo "ERROR: /usr/libexec/stone-home missing"; exit 1; }
test -x /usr/bin/steam || { echo "ERROR: /usr/bin/steam missing; STONE Home launches games through Steam"; exit 1; }
## The Game Mode session STONE Home rides along with (gamescope-session-plus, user unit).
test -e /usr/lib/systemd/user/gamescope-session-plus@.service \
    || { echo "ERROR: gamescope-session-plus@.service not found; STONE Home would never start"; exit 1; }
for f in index.html icon.png fonts/jost-latin-300-normal.woff2 grid/capsule.png boot/stone-startup.webm; do
    test -s "/usr/share/stone-home/${f}" || { echo "ERROR: STONE Home file missing: ${f}"; exit 1; }
done

### 9. STONE boot screen (Plymouth)
## The STONE theme is a Plymouth script theme; the base image does not ship the script plugin
## (first image build: "/usr/lib64/plymouth/script.so does not exist").
dnf5 -y install plymouth-plugin-script
test -e /usr/lib64/plymouth/script.so || { echo "ERROR: Plymouth script plugin missing"; exit 1; }
plymouth-set-default-theme stone
## The theme must be inside the initramfs to be seen at boot.
KVER="$(find /usr/lib/modules -mindepth 1 -maxdepth 1 -printf '%f\n' | sort | head -n1)"
dracut --no-hostonly --kver "${KVER}" --reproducible --zstd -v --add ostree \
    -f "/usr/lib/modules/${KVER}/initramfs.img"

### 10. Identity and provenance: /usr/lib/stone-release (Bazzite's os-release is left alone)
# First match only (multilib packages answer once per architecture); "not-installed" if absent.
rpmver() { local v; v="$(rpm -q --qf '%{VERSION}-%{RELEASE}\n' "$@" 2>/dev/null | grep -v 'not installed' | head -n1 || true)"; echo "${v:-not-installed}"; }
rpmfile() { local v; v="$(rpm -qf --qf '%{NAME} %{VERSION}-%{RELEASE}\n' "$1" 2>/dev/null | grep -v 'not owned\|No such\|not installed' | head -n1 || true)"; echo "${v:-not-installed}"; }
{
    echo "STONE_NAME=\"STONE OS\""
    echo "STONE_VERSION=\"${STONE_VERSION}\""
    echo "STONE_CHANNEL=\"${STONE_CHANNEL:-dev}\""
    echo "STONE_COMMIT=\"${STONE_COMMIT:-unknown}\""
    echo "STONE_BUILD_DATE=\"${STONE_BUILD_DATE:-unknown}\""
    echo "STONE_HARDWARE=\"AMD BC-250\""
    echo "STONE_BASE_IMAGE=\"${BASE_IMAGE:-unknown}\""
    echo "STONE_BASE_DIGEST=\"${BASE_DIGEST:-unknown}\""
    echo "STONE_BAZZITE_VERSION=\"$(. /usr/lib/os-release && echo "${VERSION_ID:-}${VERSION:+ (${VERSION})}")\""
    echo "STONE_KERNEL=\"${KVER}\""
    echo "STONE_MESA=\"$(rpmver mesa-vulkan-drivers)\""
    echo "STONE_GAMESCOPE=\"$(rpmfile /usr/bin/gamescope)\""
    echo "STONE_WEBKITGTK=\"$(rpmver webkitgtk6.0)\""
    echo "STONE_GOVERNOR_VERSION=\"$(rpmver cyan-skillfish-governor-smu)\""
    echo "${GOV_INFO}" | sed -n 's/^STONE_GOV_\([A-Z_]*\)=\(.*\)$/STONE_GOV_\1="\2"/p'
} > /usr/lib/stone-release
cat /usr/lib/stone-release

echo "STONE OS ${STONE_VERSION}: build completed."
