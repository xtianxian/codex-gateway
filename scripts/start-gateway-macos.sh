#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
log_dir="${project_root}/.codex-gateway/logs"
timestamp="$(date +%Y%m%d-%H%M%S)"
log_path="${log_dir}/telegram-gateway-${timestamp}.log"

mkdir -p "$log_dir"
cd "$project_root"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

exec uv run codex-gateway telegram run >>"$log_path" 2>&1
