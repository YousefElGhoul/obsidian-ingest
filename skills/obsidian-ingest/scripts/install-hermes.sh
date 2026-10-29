#!/usr/bin/env bash
set -euo pipefail

skill_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
uv_bin="$(command -v uv || true)"

if [[ -z "$uv_bin" ]]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "Install stopped: curl is required to install uv user-locally." >&2
    exit 1
  fi
  install_script="$(mktemp)"
  trap 'rm -f "$install_script"' EXIT
  if ! curl --fail --location --proto '=https' --tlsv1.2 https://astral.sh/uv/install.sh -o "$install_script"; then
    echo "Install stopped: could not download the uv installer." >&2
    exit 1
  fi
  export UV_INSTALL_DIR="${UV_INSTALL_DIR:-$HOME/.local/bin}"
  sh "$install_script"
  uv_bin="$UV_INSTALL_DIR/uv"
  if [[ ! -x "$uv_bin" ]]; then
    echo "Install stopped: uv was not found at the user-local install path $uv_bin." >&2
    exit 1
  fi
fi

if ! "$uv_bin" python find 3.13 >/dev/null 2>&1; then
  "$uv_bin" python install 3.13
fi

"$uv_bin" sync --project "$skill_root" --locked --no-dev
exec "$skill_root/.venv/bin/python" -m obsidian_ingest.hermes_install \
  --skill-root "$skill_root" --uv-bin "$uv_bin" "$@"
