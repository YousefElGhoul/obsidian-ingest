# shellcheck shell=bash
# Shared helpers for the live-trial kit. Sourced by the other scripts, never run.
# See docs/live-trials.md. Everything here fails closed: an unexpected state stops
# the script instead of guessing.

set -euo pipefail

TRIAL_MARKER=.obsidian-ingest-trial-root
TRIAL_HOME_MARKER=.obsidian-ingest-trial-home
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)

die() {
  printf 'trial: %s\n' "$*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing required program: $1"
}

# trial_init <harness>: validate the harness and set HARNESS, ROOT and THOME.
trial_init() {
  [ "$#" -ge 1 ] || die "the first argument must be a harness: claude, codex, opencode or hermes"
  HARNESS=$1
  case $HARNESS in
    claude | codex | opencode | hermes) ;;
    *) die "unknown harness '$HARNESS' (expected claude, codex, opencode or hermes)" ;;
  esac
  [ -n "${HOME:-}" ] && [ -d "$HOME" ] || die "HOME is not set to a directory"
  [ ! -e "$HOME/$TRIAL_HOME_MARKER" ] || die "this is already a trial sandbox; run the kit from the real session"
  case $HOME in
    *'"'* | *'\'* | *$'\n'*) die "HOME contains a quote, backslash or newline" ;;
  esac
  local base=${TRIAL_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/obsidian-ingest-trials}
  case $base in
    /*) ;;
    *) die "TRIAL_ROOT must be an absolute path" ;;
  esac
  ROOT=$base/$HARNESS
  THOME=$ROOT/home
  HERMES_PROFILE=${TRIAL_HERMES_PROFILE:-oikit}
}

# hermes_recorded_profile: use the profile name that prepare recorded, never the
# environment, so teardown can only delete the profile this trial created.
hermes_recorded_profile() {
  [ -f "$ROOT/hermes-profile" ] || die "no recorded Hermes profile in $ROOT (prepare did not create one)"
  HERMES_PROFILE=$(head -n 1 -- "$ROOT/hermes-profile")
  case $HERMES_PROFILE in
    "" | default | *[!A-Za-z0-9_-]*) die "recorded Hermes profile name is not usable: '$HERMES_PROFILE'" ;;
  esac
}

# confined_dir <path>: the directory exists (or can be created) under the trial home
# without passing through a symlink. A child can leave symlinks behind; host-side
# writes must not follow them out of the trial root.
confined_dir() {
  local rel=${1#"$THOME"/} cur=$THOME part
  [ "$rel" != "$1" ] || die "$1 is not under the trial home"
  local IFS=/
  for part in $rel; do
    cur=$cur/$part
    [ ! -L "$cur" ] || die "$cur is a symlink; refusing to write through it"
    if [ ! -e "$cur" ]; then
      mkdir -- "$cur"
    elif [ ! -d "$cur" ]; then
      die "$cur is not a directory"
    fi
  done
}

# root_check: the trial root exists, carries the marker, and is not a dangerous path.
root_check() {
  [ -d "$ROOT" ] || die "no trial root at $ROOT; run: scripts/trials/prepare $HARNESS"
  local real
  real=$(realpath -e -- "$ROOT")
  [ -f "$real/$TRIAL_MARKER" ] || die "$real has no $TRIAL_MARKER marker; refusing to treat it as a trial root"
  local home_real
  home_real=$(realpath -e -- "$HOME")
  case $home_real/ in
    "$real"/*) die "the trial root $real contains the home directory" ;;
  esac
  case $REPO/ in
    "$real"/*) die "the trial root $real contains the repository" ;;
  esac
  ROOT=$real
  THOME=$ROOT/home
}

# resolve_bin <name>: the real binary behind a command (mise shims break under a
# throwaway HOME, so look through them).
resolve_bin() {
  local p=""
  if command -v mise >/dev/null 2>&1; then
    p=$(mise which "$1" 2>/dev/null) || p=""
  fi
  [ -n "$p" ] || p=$(command -v "$1" 2>/dev/null) || return 1
  realpath -e -- "$p"
}

# ro_bind_for <real binary>: what to bind read-only so the binary works under the
# throwaway home. Nothing for binaries outside HOME (the read-only / covers them).
ro_bind_for() {
  case $1 in
    "$HOME"/*) ;;
    *) return 0 ;;
  esac
  if [[ $1 =~ ^(.*/mise/installs/[^/]+/[^/]+)/ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    printf '%s\n' "$1"
  fi
}

# harness_table: the per-harness facts. Sets H_BIN (command name), H_AUTO (auto-approve
# flags, allowed only inside the sandbox), H_ENV (name value pairs), H_CRED_SRC and
# H_CRED_DST (login file to copy, relative to the real and throwaway homes).
harness_table() {
  H_AUTO=()
  H_ENV=()
  H_CRED_SRC=""
  H_CRED_DST=""
  case $HARNESS in
    claude)
      H_BIN=claude
      H_AUTO=(--permission-mode bypassPermissions)
      H_ENV=(CLAUDE_CONFIG_DIR "$HOME/.claude")
      H_CRED_SRC=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json
      H_CRED_DST=.claude/.credentials.json
      ;;
    codex)
      H_BIN=codex
      H_AUTO=(--dangerously-bypass-approvals-and-sandbox)
      H_ENV=(CODEX_HOME "$HOME/.codex")
      H_CRED_SRC=${CODEX_HOME:-$HOME/.codex}/auth.json
      H_CRED_DST=.codex/auth.json
      ;;
    opencode)
      H_BIN=opencode
      H_AUTO=(--auto)
      ;;
    hermes)
      H_BIN=hermes
      ;;
  esac
}

# sandbox_plan: resolve binaries and fill BINDS (read-only binds under HOME) and
# SANDBOX_PATH. Needs harness_table first.
sandbox_plan() {
  BINDS=()
  local dirs=() name real bind
  for name in "$H_BIN" uv node; do
    if ! real=$(resolve_bin "$name"); then
      [ "$name" = node ] && continue # node is optional
      die "cannot find the '$name' binary"
    fi
    dirs+=("$(dirname -- "$real")")
    bind=$(ro_bind_for "$real")
    [ -z "$bind" ] || BINDS+=("$bind")
  done
  local extra
  if [ -n "${TRIAL_EXTRA_RO_BINDS:-}" ]; then
    IFS=: read -r -a extra <<<"$TRIAL_EXTRA_RO_BINDS"
    for bind in "${extra[@]}"; do
      [ -e "$bind" ] || die "TRIAL_EXTRA_RO_BINDS entry does not exist: $bind"
      BINDS+=("$bind")
    done
  fi
  SANDBOX_PATH=$(
    IFS=:
    printf '%s' "${dirs[*]}"
  ):/usr/local/bin:/usr/bin:/bin
}

# trial_bwrap_pids: PIDs of this user's bwrap processes that mount this trial home.
trial_bwrap_pids() {
  local p pid
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    [ -O "$p" ] || continue
    [ "$(cat "$p/comm" 2>/dev/null || true)" = bwrap ] || continue
    local cmdline
    cmdline=$(tr '\0' '\n' <"$p/cmdline" 2>/dev/null || true)
    if grep -Fxq -- "$THOME" <<<"$cmdline"; then
      printf '%s\n' "$pid"
    fi
  done
}

# hermes_profile_exists: true when the throwaway Hermes profile is listed.
hermes_profile_exists() {
  local listing
  listing=$(hermes profile list 2>/dev/null) || die "cannot list Hermes profiles"
  grep -Eq "(^|[^[:alnum:]_-])$HERMES_PROFILE([^[:alnum:]_-]|\$)" <<<"$listing"
}
