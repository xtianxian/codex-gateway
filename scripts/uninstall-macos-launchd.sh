#!/usr/bin/env bash
set -euo pipefail

label="${CODEX_GATEWAY_MACOS_LAUNCHD_LABEL:-com.codex.gateway.telegram}"

usage() {
  cat <<'EOF'
Usage: bash scripts/uninstall-macos-launchd.sh [--label <launchd-label>]

Options:
  --label NAME   Override the launchd label. Default: com.codex.gateway.telegram
  -h, --help     Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --label)
      if [ "$#" -lt 2 ]; then
        echo "--label requires a value." >&2
        exit 2
      fi
      label="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "scripts/uninstall-macos-launchd.sh must be run on macOS." >&2
  exit 1
fi

plist_path="${HOME}/Library/LaunchAgents/${label}.plist"
launchd_domain="gui/$(id -u)"

launchctl bootout "$launchd_domain" "$plist_path" >/dev/null 2>&1 || true
rm -f "$plist_path"

echo "Removed launchd service: $label"
