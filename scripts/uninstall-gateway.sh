#!/usr/bin/env bash
set -euo pipefail

env_file=".env"
state_dir=""
launchd_label="${CODEX_GATEWAY_MACOS_LAUNCHD_LABEL:-com.codex.gateway.telegram}"
systemd_unit="codex-gateway.service"
skip_process_cleanup=0
docker_gateway_volumes=0
dry_run=0

usage() {
  cat <<'EOF'
Usage: bash scripts/uninstall-gateway.sh [options]

Options:
  --env-file PATH             Environment file to parse and remove. Default: .env
  --state-dir PATH            Explicit Telegram state directory to remove.
  --launchd-label NAME        macOS launchd label. Default: com.codex.gateway.telegram
  --systemd-unit NAME         Linux systemd unit. Default: codex-gateway.service
  --skip-process-cleanup      Do not stop matching gateway/app-server processes.
  --docker-gateway-volumes    Stop Compose and remove only gateway config/state volumes.
  --dry-run                   Print intended removals without deleting files or stopping services.
  -h, --help                  Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      if [ "$#" -lt 2 ]; then
        echo "--env-file requires a value." >&2
        exit 2
      fi
      env_file="$2"
      shift
      ;;
    --state-dir)
      if [ "$#" -lt 2 ]; then
        echo "--state-dir requires a value." >&2
        exit 2
      fi
      state_dir="$2"
      shift
      ;;
    --launchd-label)
      if [ "$#" -lt 2 ]; then
        echo "--launchd-label requires a value." >&2
        exit 2
      fi
      launchd_label="$2"
      shift
      ;;
    --systemd-unit)
      if [ "$#" -lt 2 ]; then
        echo "--systemd-unit requires a value." >&2
        exit 2
      fi
      systemd_unit="$2"
      shift
      ;;
    --skip-process-cleanup)
      skip_process_cleanup=1
      ;;
    --docker-gateway-volumes)
      docker_gateway_volumes=1
      ;;
    --dry-run)
      dry_run=1
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

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
default_project_root="$(cd -- "${script_dir}/.." && pwd)"
project_root="${CODEX_GATEWAY_TEST_PROJECT_ROOT:-$default_project_root}"
compose_file="${project_root}/testing/docker/compose.linux.yaml"
gateway_volumes=(codex-gateway-linux_gateway-config codex-gateway-linux_gateway-state)
env_keys=()
env_values=()
workspace_roots=()
state_targets=()

notice() {
  printf '%s\n' "$*"
}

resolve_path() {
  local raw="$1"
  local expanded="$raw"
  case "$expanded" in
    "~")
      expanded="$HOME"
      ;;
    "~/"*)
      expanded="$HOME/${expanded#~/}"
      ;;
  esac
  expanded="${expanded/#\$HOME/$HOME}"
  if [ "${expanded#/}" = "$expanded" ]; then
    expanded="${project_root}/${expanded}"
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$expanded" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expandvars(os.path.expanduser(sys.argv[1]))))
PY
  elif command -v python >/dev/null 2>&1; then
    python - "$expanded" <<'PY'
import os
import sys

print(os.path.abspath(os.path.expandvars(os.path.expanduser(sys.argv[1]))))
PY
  else
    local dir base
    dir="$(dirname -- "$expanded")"
    base="$(basename -- "$expanded")"
    if cd "$dir" 2>/dev/null; then
      printf '%s/%s\n' "$(pwd -P)" "$base"
    else
      printf '%s\n' "$expanded"
    fi
  fi
}

same_path() {
  [ "$1" = "$2" ]
}

ancestor_path() {
  local ancestor="${1%/}"
  local descendant="${2%/}"
  [ "$ancestor" = "$descendant" ] || [ "${descendant#"$ancestor"/}" != "$descendant" ]
}

read_dotenv() {
  local path="$1"
  local line trimmed key value
  [ -f "$path" ] || return 0

  while IFS= read -r line || [ -n "$line" ]; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [ -n "$trimmed" ] || continue
    case "$trimmed" in
      \#*)
        continue
        ;;
      export\ *)
        trimmed="${trimmed#export }"
        ;;
    esac
    case "$trimmed" in
      *=*)
        key="${trimmed%%=*}"
        value="${trimmed#*=}"
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        case "$value" in
          \"*\")
            value="${value#\"}"
            value="${value%\"}"
            ;;
          \'*\')
            value="${value#\'}"
            value="${value%\'}"
            ;;
        esac
        env_keys+=("$key")
        env_values+=("$value")
        ;;
    esac
  done < "$path"
}

env_get() {
  local wanted="$1"
  local index
  index=0
  while [ "$index" -lt "${#env_keys[@]}" ]; do
    if [ "${env_keys[$index]}" = "$wanted" ]; then
      printf '%s\n' "${env_values[$index]}"
      return 0
    fi
    index=$((index + 1))
  done
  return 1
}

append_workspace_roots_from_value() {
  local value="$1"
  local old_ifs part
  value="$(printf '%s' "$value" | tr ';,' '\n\n')"
  old_ifs="$IFS"
  IFS='
'
  for part in $value; do
    part="${part#"${part%%[![:space:]]*}"}"
    part="${part%"${part##*[![:space:]]}"}"
    [ -n "$part" ] || continue
    workspace_roots+=("$(resolve_path "$part")")
  done
  IFS="$old_ifs"
}

collect_workspace_roots() {
  local value key
  for key in CODEX_GATEWAY_ALLOWED_ROOTS CODEX_ALLOWED_ROOTS; do
    if value="$(env_get "$key")"; then
      append_workspace_roots_from_value "$value"
    fi
  done
  for key in CODEX_GATEWAY_DEFAULT_CWD CODEX_DEFAULT_CWD; do
    if value="$(env_get "$key")"; then
      workspace_roots+=("$(resolve_path "$value")")
    fi
  done
}

assert_safe_delete_target() {
  local path="${1%/}"
  local root leaf profile codex_home workspace
  root="$(cd / && pwd)"
  if same_path "$path" "$root"; then
    echo "Refusing to remove filesystem root: $path" >&2
    exit 1
  fi
  if same_path "$path" "$project_root"; then
    echo "Refusing to remove the repository root: $path" >&2
    exit 1
  fi

  for profile in "${HOME:-}" "${TMPDIR:-}" "${XDG_CONFIG_HOME:-}" "${XDG_STATE_HOME:-}"; do
    [ -n "$profile" ] || continue
    profile="$(resolve_path "$profile")"
    if same_path "$path" "$profile"; then
      echo "Refusing to remove profile or broad state directory: $path" >&2
      exit 1
    fi
  done

  codex_home="$(resolve_path "${CODEX_HOME:-${HOME}/.codex}")"
  if same_path "$path" "$codex_home"; then
    echo "Refusing to remove Codex auth directory: $path" >&2
    exit 1
  fi

  for workspace in "${workspace_roots[@]}"; do
    [ -n "$workspace" ] || continue
    if same_path "$path" "$workspace"; then
      echo "Refusing to remove configured workspace root: $path" >&2
      exit 1
    fi
    if ancestor_path "$path" "$workspace"; then
      echo "Refusing to remove parent of configured workspace root: $path" >&2
      exit 1
    fi
  done

  leaf="$(basename -- "$path")"
  case "$leaf" in
    Users|Documents|Desktop|Downloads|Projects|src|repo|repos|workspaces|workspace|tmp|temp|var|opt|etc|usr|mnt|media)
      echo "Refusing to remove broad parent directory: $path" >&2
      exit 1
      ;;
  esac
}

gateway_owned_state_path() {
  local path="$1"
  local repo_gateway
  repo_gateway="$(resolve_path ".codex-gateway")"
  if ancestor_path "$repo_gateway" "$path"; then
    return 0
  fi
  case "/$path/" in
    */.codex-gateway/*|*/codex-gateway/*)
      return 0
      ;;
  esac
  return 1
}

remove_target() {
  local path="$1"
  local description="$2"

  assert_safe_delete_target "$path"
  if [ ! -e "$path" ]; then
    notice "Already absent: ${description} (${path})"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    notice "DRY RUN: remove ${description} (${path})"
    return 0
  fi

  rm -rf -- "$path"
  notice "Removed: ${description} (${path})"
}

run_cmd() {
  if [ "${CODEX_GATEWAY_TEST_DOCKER_LOG:-}" ]; then
    printf '%s\n' "$*" >> "$CODEX_GATEWAY_TEST_DOCKER_LOG"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    notice "DRY RUN: $*"
    return 0
  fi
  "$@" || notice "Command failed or was unavailable: $*"
}

sudo_prefix() {
  if [ "$(id -u)" -eq 0 ]; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    printf '%s\n' sudo
  fi
}

cleanup_startup() {
  local os plist launchd_domain unit_path sudo_cmd
  os="$(uname -s)"
  case "$os" in
    Darwin)
      plist="${HOME}/Library/LaunchAgents/${launchd_label}.plist"
      launchd_domain="gui/$(id -u)"
      run_cmd launchctl bootout "$launchd_domain" "$plist"
      remove_target "$plist" "launchd plist"
      ;;
    Linux)
      unit_path="/etc/systemd/system/${systemd_unit}"
      sudo_cmd="$(sudo_prefix || true)"
      if command -v systemctl >/dev/null 2>&1; then
        if [ -n "$sudo_cmd" ]; then
          run_cmd "$sudo_cmd" systemctl disable --now "$systemd_unit"
        else
          run_cmd systemctl disable --now "$systemd_unit"
        fi
      else
        notice "systemctl was not found; skipped systemd unit disable."
      fi

      if [ -e "$unit_path" ]; then
        if [ "$dry_run" -eq 1 ]; then
          notice "DRY RUN: remove systemd unit (${unit_path})"
        elif [ -n "$sudo_cmd" ]; then
          "$sudo_cmd" rm -f -- "$unit_path"
        else
          rm -f -- "$unit_path"
        fi
        if command -v systemctl >/dev/null 2>&1; then
          if [ -n "$sudo_cmd" ]; then
            run_cmd "$sudo_cmd" systemctl daemon-reload
          else
            run_cmd systemctl daemon-reload
          fi
        fi
      else
        notice "No systemd unit file found: ${unit_path}"
      fi
      ;;
    *)
      notice "No macOS launchd or Linux systemd startup cleanup for OS: ${os}"
      ;;
  esac
}

cleanup_processes() {
  local line pid command
  if [ "$skip_process_cleanup" -eq 1 ]; then
    notice "Skipped process cleanup."
    return 0
  fi
  if ! command -v ps >/dev/null 2>&1; then
    notice "ps was not found; skipped process cleanup."
    return 0
  fi

  while IFS= read -r line; do
    pid="${line#"${line%%[![:space:]]*}"}"
    pid="${pid%%[[:space:]]*}"
    command="${line#*[[:space:]]}"
    [ -n "$pid" ] || continue
    [ "$pid" != "$$" ] || continue
    case "$command" in
      *"$project_root"*codex-gateway*|*"$project_root"*"codex app-server"*|*"$project_root"*"codex"*"app-server"*)
        if [ "$dry_run" -eq 1 ]; then
          notice "DRY RUN: stop matching gateway process PID ${pid}"
        else
          kill "$pid" >/dev/null 2>&1 || true
          notice "Stopped matching gateway process: PID ${pid}"
        fi
        ;;
    esac
  done <<EOF
$(ps -axo pid=,command= 2>/dev/null || true)
EOF
}

cleanup_docker_volumes() {
  [ "$docker_gateway_volumes" -eq 1 ] || return 0

  run_cmd docker compose -f "$compose_file" down --remove-orphans
  run_cmd docker volume rm "${gateway_volumes[@]}"
}

collect_state_targets() {
  local value resolved key explicit_seen
  explicit_seen=""
  if [ -n "$state_dir" ]; then
    resolved="$(resolve_path "$state_dir")"
    assert_safe_delete_target "$resolved"
    state_targets+=("$resolved")
    explicit_seen="$resolved"
  fi

  for key in CODEX_GATEWAY_TELEGRAM_STATE_DIR CODEX_TELEGRAM_STATE_DIR; do
    if value="$(env_get "$key")"; then
      [ -n "$value" ] || continue
      resolved="$(resolve_path "$value")"
      [ "$resolved" != "$explicit_seen" ] || continue
      if ! gateway_owned_state_path "$resolved"; then
        notice "Skipped configured Telegram state dir because it is not clearly gateway-owned. Pass --state-dir to remove it explicitly: ${resolved}"
        continue
      fi
      assert_safe_delete_target "$resolved"
      state_targets+=("$resolved")
    fi
  done
}

resolved_env_file="$(resolve_path "$env_file")"
read_dotenv "$resolved_env_file"
collect_workspace_roots
assert_safe_delete_target "$resolved_env_file"
assert_safe_delete_target "$(resolve_path ".codex-gateway")"
collect_state_targets

notice "Codex Gateway full uninstall removes gateway config, state, startup integration, and logs only."
notice "Codex CLI login/auth, workspaces, Docker codex-home, and Telegram BotFather token state are preserved."

cleanup_startup
cleanup_processes
remove_target "$resolved_env_file" "environment file"
remove_target "$(resolve_path ".codex-gateway")" "repo-local gateway state/logs"
for state_target in "${state_targets[@]}"; do
  remove_target "$state_target" "Telegram state directory"
done
cleanup_docker_volumes

notice "Manual follow-up: revoke or rotate the Telegram bot token through BotFather only if you want the bot token invalidated."
notice "Codex CLI login/auth was not removed."
