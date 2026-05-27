#!/usr/bin/env bash
set -euo pipefail

install_uv=0
remove_startup=0
skip_telegram_setup=0
skip_startup=0

usage() {
  cat <<'EOF'
Usage: bash scripts/setup-macos.sh [options]

Options:
  --install-uv            Install uv with the official installer when missing.
  --remove-startup        Remove the macOS launchd user service and exit.
  --skip-telegram-setup   Sync dependencies and run tests without setup prompts.
  --skip-startup          Do not prompt to install/start the launchd service.
  -h, --help              Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-uv)
      install_uv=1
      ;;
    --remove-startup)
      remove_startup=1
      ;;
    --skip-telegram-setup)
      skip_telegram_setup=1
      ;;
    --skip-startup)
      skip_startup=1
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
  echo "scripts/setup-macos.sh must be run on macOS." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
install_launchd_script="${script_dir}/install-macos-launchd.sh"
uninstall_launchd_script="${script_dir}/uninstall-macos-launchd.sh"
pytest_temp_root="${TMPDIR:-/tmp}/codex-gateway/pytest-temp"
launchd_label="${CODEX_GATEWAY_MACOS_LAUNCHD_LABEL:-com.codex.gateway.telegram}"
launchd_domain="gui/$(id -u)"
launchd_plist_path="${HOME}/Library/LaunchAgents/${launchd_label}.plist"

read_yes_no() {
  local prompt="$1"
  local default="$2"
  local suffix answer
  if [ "$default" = "yes" ]; then
    suffix="[Y/n]"
  else
    suffix="[y/N]"
  fi

  while true; do
    printf '%s %s ' "$prompt" "$suffix"
    if ! IFS= read -r answer; then
      [ "$default" = "yes" ]
      return
    fi
    case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
      "")
        [ "$default" = "yes" ]
        return
        ;;
      y|yes)
        return 0
        ;;
      n|no)
        return 1
        ;;
      *)
        echo "Please answer y or n."
        ;;
    esac
  done
}

launchd_service_exists() {
  if [ "${CODEX_GATEWAY_TEST_LAUNCHD_EXISTS+x}" ]; then
    case "$(printf '%s' "$CODEX_GATEWAY_TEST_LAUNCHD_EXISTS" | tr '[:upper:]' '[:lower:]')" in
      1|true|yes)
        return 0
        ;;
      *)
        return 1
        ;;
    esac
  fi

  [ -f "$launchd_plist_path" ] || launchctl print "${launchd_domain}/${launchd_label}" >/dev/null 2>&1
}

if [ "$remove_startup" -eq 1 ]; then
  bash "$uninstall_launchd_script"
  exit 0
fi

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is missing."
  echo "Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "Or rerun this setup script with:"
  echo "  bash scripts/setup-macos.sh --install-uv"
  if [ "$install_uv" -ne 1 ]; then
    exit 1
  fi
  echo
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv installation completed, but uv was not found on PATH. Open a new shell or add uv to PATH, then rerun setup." >&2
    exit 1
  fi
fi

if command -v codex >/dev/null 2>&1; then
  codex --version || true
  codex login status || true
else
  echo "Codex CLI was not found on PATH."
  echo "Install it with:"
  echo "  npm install -g @openai/codex@0.133.0"
fi

cd "$project_root"

echo "Syncing dependencies with uv..."
uv sync --extra dev

echo "Running tests..."
mkdir -p "$pytest_temp_root"
PYTEST_DEBUG_TEMPROOT="$pytest_temp_root" \
  PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }-p no:cacheprovider" \
  uv run pytest

ran_telegram_setup=0
if [ "$skip_telegram_setup" -ne 1 ] && read_yes_no "Run Telegram setup now?" "yes"; then
  ran_telegram_setup=1
  uv run codex-gateway telegram setup
fi

started_gateway=0
launchd_exists=0
startup_prompt="Install and start Codex Gateway as a macOS launchd user service?"
if launchd_service_exists; then
  launchd_exists=1
  startup_prompt="Update and restart existing Codex Gateway launchd user service to apply current .env?"
fi
if [ "$skip_startup" -ne 1 ] && read_yes_no "$startup_prompt" "no"; then
  started_gateway=1
  bash "$install_launchd_script" --start
fi

if [ "$ran_telegram_setup" -eq 1 ]; then
  echo
  if [ "$started_gateway" -eq 1 ]; then
    echo "Codex Gateway can receive Telegram messages now."
    echo "Send /start from the configured Telegram user to get the pairing command."
  elif [ "$launchd_exists" -eq 1 ]; then
    echo "Existing launchd service was not restarted. Run this to apply .env changes:"
    echo "  bash scripts/install-macos-launchd.sh --start"
  else
    echo "Start the gateway, then send /start from the configured Telegram user to get the pairing command:"
    echo "  uv run codex-gateway telegram run"
  fi
fi

echo
echo "Next steps:"
if [ "$ran_telegram_setup" -ne 1 ]; then
  echo "  uv run codex-gateway telegram setup"
fi
echo "  uv run codex-gateway telegram status"
if [ "$started_gateway" -ne 1 ]; then
  echo "  bash scripts/install-macos-launchd.sh --start"
  if [ "$launchd_exists" -ne 1 ]; then
    echo "  uv run codex-gateway telegram run"
  fi
fi
