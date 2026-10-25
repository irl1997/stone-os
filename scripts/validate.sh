#!/usr/bin/env bash
# Structure and configuration checks before any build. Prints PASS or FAIL.
#   scripts/validate.sh
# Tools: python3 (with gi), shellcheck, xmllint, systemd-analyze, ruff, actionlint.
# A missing tool is a FAIL when STONE_REQUIRE_ALL=1 (CI), a SKIP otherwise.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PY="${PYTHON:-python3}"
fails=0; skips=0
ok()   { echo "PASS  $*"; }
bad()  { echo "FAIL  $*"; fails=$((fails + 1)); }
skip() { if [[ "${STONE_REQUIRE_ALL:-0}" == 1 ]]; then bad "$* (tool missing)"; else echo "SKIP  $*"; skips=$((skips + 1)); fi; }
have() { command -v "$1" >/dev/null 2>&1; }

# 1. required files
for f in Containerfile VERSION LICENSE README.md BUILD.md INSTALL.md RECOVERY.md ARCHITECTURE.md HARDWARE_VALIDATION.md \
         RELEASE_NOTES.md DEPENDENCIES.md GITHUB_SETUP.md SECURITY.md config/build.env disk_config/iso.toml \
         build_files/build.sh build_files/check-kernel.sh build_files/governor-config.py \
         .github/workflows/ci.yml .github/workflows/build-image.yml .github/workflows/build-installer.yml .github/workflows/release.yml \
         system_files/usr/bin/stoned system_files/usr/bin/stonectl system_files/usr/bin/stone-home system_files/usr/bin/stone-home-session \
         system_files/usr/share/stone-home/index.html system_files/usr/share/stone-home/icon.png \
         system_files/usr/share/stone-home/boot/stone-startup.webm; do
    [[ -s "$f" ]] || bad "missing or empty: $f"
done
ok "required files present"

# 2. version
v="$(tr -d '[:space:]' < VERSION)"
if [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]]; then ok "VERSION $v"; else bad "VERSION '$v' is not X.Y.Z[-pre]"; fi
if grep -rqE 'STONE_VERSION="?[0-9]' system_files 2>/dev/null; then bad "a version is hard-coded under system_files (VERSION is the only source)"; else ok "single version source"; fi

# release facts must survive `eval` (values with spaces; first GitHub release run)
if out="$(bash -c 'set -euo pipefail; eval "$(scripts/release-info.sh "v$(tr -d "[:space:]" < VERSION)" 0123456789abcdef --shell)"; echo "${channel}|${tags}"' 2>&1)" \
        && [[ "${out}" == *"|"* && "${out}" != "|"* ]]; then ok "release-info for shells (${out})"; else bad "release-info --shell: ${out}"; fi

# 3. executables
for f in build_files/build.sh build_files/check-kernel.sh build_files/governor-config.py scripts/*.sh system_files/usr/bin/* system_files/usr/libexec/*; do
    [[ -x "$f" ]] || bad "not executable: $f"
done
ok "executable bits"

# 4. shell
if have shellcheck; then
    if shellcheck -x build_files/*.sh scripts/*.sh system_files/usr/bin/stone-home; then ok "shellcheck"; else bad "shellcheck"; fi
else skip "shellcheck"; fi

# 5. python
if $PY -m py_compile system_files/usr/lib/stone/stoned/*.py system_files/usr/lib/stone/stone_home/*.py \
        build_files/governor-config.py tests/*.py development/*.py 2>/dev/null; then ok "python compiles"; else bad "python compile"; fi
if have ruff; then
    if ruff check --select E4,E9,F,W,B --ignore B008 --target-version py311 system_files/usr/lib/stone build_files tests development; then ok "ruff"; else bad "ruff"; fi
else skip "ruff"; fi

# 6. JSON / XML / TOML
if $PY - <<'PY'
import json, sys, tomllib
for p in ("system_files/usr/lib/stone/stability.json", "tests/e2e/package.json"):
    json.load(open(p))
tomllib.load(open("disk_config/iso.toml", "rb"))
PY
then ok "json and toml"; else bad "json/toml"; fi
if have xmllint; then
    if xmllint --noout system_files/usr/share/polkit-1/actions/*.policy system_files/usr/share/dbus-1/system.d/*.conf; then ok "xml"; else bad "xml"; fi
# stoned is not root: it may ask polkit about callers only as an owner of each action.
n_act=$(grep -c '<action id=' system_files/usr/share/polkit-1/actions/org.stonemachine.performance.policy)
n_own=$(grep -c 'org.freedesktop.policykit.owner">unix-user:stoned<' system_files/usr/share/polkit-1/actions/org.stonemachine.performance.policy)
if [[ "$n_act" -gt 0 && "$n_act" == "$n_own" ]]; then ok "polkit actions owned by stoned"; else bad "every polkit action needs org.freedesktop.policykit.owner = unix-user:stoned"; fi
# polkit rule: grants the STONE actions (and power) to wheel only, nothing else.
if command -v node >/dev/null 2>&1; then
    if node -e "
let rule; const polkit = { Result: { YES: 'yes' }, addRule: f => { rule = f; } };
eval(require('fs').readFileSync('system_files/usr/share/polkit-1/rules.d/50-stone.rules', 'utf8'));
const s = g => ({ isInGroup: x => x === g }), a = id => ({ id });
const ok = rule(a('org.stonemachine.performance.change'), s('wheel')) === 'yes' && rule(a('org.stonemachine.performance.tune'), s('wheel')) === 'yes'
  && rule(a('org.stonemachine.performance.change'), s('users')) === undefined && rule(a('org.freedesktop.udisks2.modify'), s('wheel')) === undefined;
process.exit(ok ? 0 : 1);"; then ok "polkit rule"; else bad "polkit rule grants the wrong things"; fi
else
    skip "polkit rule (node not installed)"
fi
else skip "xmllint"; fi

# 7. systemd units
if have systemd-analyze; then
    root="$(mktemp -d)"; mkdir -p "$root/usr/lib/systemd/system"
    cp -a /usr/lib/systemd/system/. "$root/usr/lib/systemd/system/" 2>/dev/null; cp -a /lib/systemd/system/. "$root/usr/lib/systemd/system/" 2>/dev/null
    cp -a system_files/. "$root/"
    if systemd-analyze verify --man=no --root="$root" "$root"/usr/lib/systemd/system/stoned*.service; then ok "systemd-analyze verify (system units)"; else bad "systemd-analyze verify"; fi
    rm -rf "$root"
else skip "systemd-analyze"; fi
if $PY - <<'PY'
import configparser, glob, sys
ALLOWED = {"Unit": {"Description", "Before", "After", "PartOf", "Wants", "Requires"},
           "Service": {"Type", "ExecStart", "TimeoutStartSec", "SuccessExitStatus", "Environment"}}
bad = 0
for f in glob.glob("system_files/usr/lib/systemd/user/**/*.service", recursive=True) + \
         glob.glob("system_files/usr/lib/systemd/user/**/*.conf", recursive=True):
    c = configparser.ConfigParser(strict=True, interpolation=None)
    c.optionxform = str
    c.read(f)
    for s in c.sections():
        for k in c[s]:
            if k not in ALLOWED.get(s, set()):
                print(f"{f}: unexpected [{s}] {k}"); bad = 1
    for k, v in c.items("Service") if c.has_section("Service") else []:
        if k == "ExecStart" and not v.startswith("/usr/bin/stone-home-session "):
            print(f"{f}: ExecStart must be stone-home-session"); bad = 1
# test switches must never be set by a shipped unit
for f in glob.glob("system_files/usr/lib/systemd/**/*", recursive=True):
    try:
        t = open(f).read()
    except (IsADirectoryError, UnicodeDecodeError):
        continue
    for sw in ("STONE_POLKIT", "STONE_BUS", "STONE_GAME_SOURCE", "STONE_SYSFS_ROOT", "STONE_STATE_DIR", "STONE_CMDLINE",
               "STONE_STEAM_ROOT", "STONE_CTL_ALLOW_USER", "STONE_MIN_SESSION_S", "STONE_TEST_SECONDS", "STONE_THERMAL_SCALE", "STONE_STABILITY_CONF"):
        if sw in t:
            print(f"{f}: sets test switch {sw}"); bad = 1
sys.exit(bad)
PY
then ok "user units and no test switches in shipped units"; else bad "user units / test switches"; fi

# 8. production vs development: no simulation in what the image ships
if grep -rnE "SimStoned|PerfAPI\.sim|fake_governor|Math\.random\(\) \* 8|FAKE_GOV" system_files; then bad "simulation code under system_files"
else ok "no simulated hardware in system_files"; fi
if grep -rnE "\b(TODO|FIXME|XXX)\b|CAMBIAR-POR|PLACEHOLDER" --include='*' system_files build_files Containerfile config disk_config .github 2>/dev/null \
        | grep -v '@IMAGE_REF@\|@UPDATE_REF@'; then bad "placeholders in shipped files"; else ok "no placeholders in shipped files"; fi

# 9. STONE Home page is generated from the prototype and up to date
if $PY development/build_home.py --check; then ok "STONE Home page up to date"; else bad "STONE Home page out of date"; fi

# 10. workflows
if have actionlint; then
    if actionlint -config-file .github/actionlint.yaml .github/workflows/*.yml; then ok "actionlint"; else bad "actionlint"; fi
else skip "actionlint"; fi
if grep -nE "uses: [^ ]+@(main|master|latest|v[0-9]+)\s*$" .github/workflows/*.yml; then bad "unpinned action (use a commit SHA)"; else ok "actions pinned by SHA"; fi
for w in .github/workflows/*.yml; do grep -q "^permissions:" "$w" || bad "$w: no top-level permissions"; done
ok "workflow permissions declared"

# 11. Containerfile
if grep -q "^RUN bootc container lint" Containerfile; then ok "bootc lint step"; else bad "Containerfile without bootc container lint"; fi

echo
if [[ $fails -eq 0 ]]; then echo "VALIDATE: PASS (${skips} skipped)"; exit 0; fi
echo "VALIDATE: FAIL (${fails} failed, ${skips} skipped)"; exit 1
