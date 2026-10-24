#!/usr/bin/env bash
# Prints the release facts of a build as KEY=value lines (for $GITHUB_OUTPUT or `source`).
#   scripts/release-info.sh <git-ref-name> <commit-sha> [--shell]
# Default output is KEY=value for $GITHUB_OUTPUT (values may contain spaces). With --shell every
# value is single-quoted, for `eval` in a shell script.
# The version comes only from ./VERSION. A tag build must be tagged v<VERSION> exactly.
#   v0.1.0-rc1  -> channel rc      tags: 0.1.0-rc1 rc
#   v0.2.0-beta1-> channel beta    tags: 0.2.0-beta1 beta
#   v1.0.0      -> channel stable  tags: 1.0.0 stable latest
#   anything else (main, manual)   -> channel dev  tags: dev dev-<sha7>
set -euo pipefail
cd "$(dirname "$0")/.."
ref="${1:-}"; sha="${2:-unknown}"; shell="${3:-}"
version="$(tr -d '[:space:]' < VERSION)"
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]] || { echo "VERSION '${version}' is not X.Y.Z[-pre]" >&2; exit 1; }
short="${sha:0:7}"
if [[ "${ref}" == v* ]]; then
    [[ "${ref}" == "v${version}" ]] || { echo "tag ${ref} does not match VERSION ${version}" >&2; exit 1; }
    case "${version}" in
        *-rc*)   channel=rc;     tags="${version} rc" ;;
        *-beta*) channel=beta;   tags="${version} beta" ;;
        *-*)     channel=dev;    tags="${version} dev" ;;
        *)       channel=stable; tags="${version} stable latest" ;;
    esac
    prerelease=$([[ "${channel}" == stable ]] && echo false || echo true)
else
    channel=dev; tags="dev dev-${short}"; prerelease=true
fi
out() { if [[ "${shell}" == "--shell" ]]; then printf "%s='%s'\n" "$1" "$2"; else printf '%s=%s\n' "$1" "$2"; fi; }
out version "${version}"
out channel "${channel}"
out tags "${tags}"
out primary_tag "${tags%% *}"
out prerelease "${prerelease}"
out short_sha "${short}"
