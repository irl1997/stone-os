#!/usr/bin/env bash
# Every software test of STONE OS. Prints PASS or FAIL.
#   scripts/test-all.sh
#   PYTHON=python3.12 scripts/test-all.sh     (an interpreter with PyGObject)
# Suites:
#   tests/run_tests.py       stoned: recovery, governor, telemetry, thermal, Safe Mode, games, comparison
#   tests/test_home.py       STONE Home: library, shortcut, host, bridge against a real stoned
#   tests/webkit_selftest.py the real GTK/WebKit shell against a real stoned (needs xvfb-run + WebKitGTK 6)
#   tests/e2e_home.py        the production page in Chromium, end to end (needs node + tests/e2e deps)
#   eslint                   page JavaScript: undefined names, duplicates (needs tests/e2e deps)
# With STONE_REQUIRE_ALL=1 (CI) a suite that cannot run is a failure, not a skip.
# Passing means CODE TESTED. Nothing here touches or verifies real BC-250 hardware.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PY="${PYTHON:-python3}"
fails=0; skips=0
run() { local name="$1"; shift; echo "=== ${name}"; if "$@"; then echo "PASS  ${name}"; else echo "FAIL  ${name}"; fails=$((fails + 1)); fi; }
skip() { if [[ "${STONE_REQUIRE_ALL:-0}" == 1 ]]; then echo "FAIL  $1 (cannot run: $2)"; fails=$((fails + 1)); else echo "SKIP  $1 ($2)"; skips=$((skips + 1)); fi; }

run "stoned suite" "$PY" tests/run_tests.py
run "STONE Home suite" "$PY" tests/test_home.py
if command -v xvfb-run >/dev/null && "$PY" -c "import gi; gi.require_version('WebKit','6.0'); gi.require_version('Gtk','4.0')" 2>/dev/null; then
    run "WebKit shell self-test" "$PY" tests/webkit_selftest.py
else skip "WebKit shell self-test" "xvfb-run or WebKitGTK 6 missing"; fi
if command -v node >/dev/null && [[ -d tests/e2e/node_modules/playwright ]]; then
    run "end-to-end (Chromium)" "$PY" tests/e2e_home.py
else skip "end-to-end (Chromium)" "run: (cd tests/e2e && npm ci && npx playwright install chromium)"; fi
if command -v node >/dev/null && [[ -x tests/e2e/node_modules/.bin/eslint ]]; then
    run "eslint (STONE Home page)" node tests/e2e/lint-page.mjs
else skip "eslint (STONE Home page)" "tests/e2e dependencies not installed"; fi

echo
if [[ $fails -eq 0 ]]; then echo "TEST-ALL: PASS (${skips} skipped)"; exit 0; fi
echo "TEST-ALL: FAIL (${fails} failed, ${skips} skipped)"; exit 1
