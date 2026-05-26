#!/usr/bin/env bash
set -euo pipefail

start_now=0
label="${CODEX_GATEWAY_MACOS_LAUNCHD_LABEL:-com.codex.gateway.telegram}"

usage() {
  cat <<'EOF'
Usage: bash scripts/install-macos-launchd.sh [--start] [--label <launchd-label>]

Options:
  --start        Load and start the launchd user service now.
  --label NAME   Override the launchd label. Default: com.codex.gateway.telegram
  -h, --help     Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --start)
      start_now=1
      ;;
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
  echo "scripts/install-macos-launchd.sh must be run on macOS." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
start_script="${script_dir}/start-gateway-macos.sh"
plist_dir="${HOME}/Library/LaunchAgents"
plist_path="${plist_dir}/${label}.plist"
launchd_domain="gui/$(id -u)"
log_dir="${project_root}/.codex-gateway/logs/launchd"

xml_escape() {
  printf '%s' "$1" |
    sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

mkdir -p "$plist_dir" "$log_dir"

escaped_label="$(xml_escape "$label")"
escaped_start_script="$(xml_escape "$start_script")"
escaped_project_root="$(xml_escape "$project_root")"
escaped_home="$(xml_escape "$HOME")"
escaped_codex_home="$(xml_escape "${CODEX_HOME:-${HOME}/.codex}")"
escaped_path="$(xml_escape "${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")"
escaped_stdout="$(xml_escape "${log_dir}/stdout.log")"
escaped_stderr="$(xml_escape "${log_dir}/stderr.log")"

cat > "$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${escaped_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${escaped_start_script}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${escaped_project_root}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${escaped_home}</string>
    <key>CODEX_HOME</key>
    <string>${escaped_codex_home}</string>
    <key>PATH</key>
    <string>${escaped_path}</string>
    <key>PYTHONUTF8</key>
    <string>1</string>
    <key>PYTHONIOENCODING</key>
    <string>utf-8</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${escaped_stdout}</string>
  <key>StandardErrorPath</key>
  <string>${escaped_stderr}</string>
</dict>
</plist>
EOF

plutil -lint "$plist_path" >/dev/null

echo "Installed launchd plist: $plist_path"
echo "Logs: ${project_root}/.codex-gateway/logs"

if [ "$start_now" -eq 1 ]; then
  launchctl bootout "$launchd_domain" "$plist_path" >/dev/null 2>&1 || true
  launchctl bootstrap "$launchd_domain" "$plist_path"
  launchctl enable "${launchd_domain}/${label}"
  launchctl kickstart -k "${launchd_domain}/${label}"
  echo "launchd service started: $label"
else
  echo "Load and start it now with:"
  echo "  launchctl bootstrap ${launchd_domain} ${plist_path}"
  echo "  launchctl kickstart -k ${launchd_domain}/${label}"
fi
