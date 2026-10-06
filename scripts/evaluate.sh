#!/usr/bin/env bash
# Run from the project root: bash scripts/evaluate.sh [manifest [output_dir [delay_seconds]]]
# Delay accepts nonnegative decimal seconds (no signs or exponent notation).
set -u
export LC_ALL=C

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

(( $# <= 3 )) || die 'Expected at most three arguments.'
manifest=${1:-videos.txt}
output_dir=${2:-.}
delay=${3-10}
[[ -f "$manifest" && -r "$manifest" ]] || die "Manifest is not a readable file: $manifest"
[[ -d "$output_dir" && -w "$output_dir" ]] || die "Output directory must exist and be writable: $output_dir"
[[ "$delay" =~ ^([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]] || die 'Delay must be finite, nonnegative decimal seconds.'
command -v uv >/dev/null 2>&1 || die 'uv is not available on PATH.'

ids=()
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
    (( line_number += 1 ))
    line=${line%%#*}
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [[ -n "$line" ]] || continue
    [[ "$line" =~ ^[A-Za-z0-9_-]{11}$ ]] || die "Invalid video ID at manifest line $line_number: $line"
    ids+=("$line")
done < "$manifest"

printf 'Running formatting, lint, and tests before live evaluation...\n' >&2
uv run ruff format . && uv run ruff check src tests && uv run pytest || die 'Preflight failed; no videos were attempted.'

temporary=''
cleanup() {
    if [[ -n "$temporary" ]]; then
        rm -f -- "$temporary"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

successes=0
failures=0
for index in "${!ids[@]}"; do
    if (( index > 0 )); then
        sleep "$delay" || die 'Delay interrupted or failed.'
    fi
    id=${ids[index]}
    final="$output_dir/run_output_${id}.txt"
    stderr_log="$output_dir/run_stderr_${id}.log"
    previous=false
    [[ -e "$final" || -L "$final" ]] && previous=true
    printf '[%s/%s] Attempting %s\n' "$((index + 1))" "${#ids[@]}" "$id" >&2
    # Stage beside the final report so publication is an atomic rename.
    if temporary=$(mktemp -- "$output_dir/.run_output_${id}.XXXXXX"); then
        if uv run obsidian-ingest --inspect-chunks "https://www.youtube.com/watch?v=$id" \
            < /dev/null > "$temporary" 2> "$stderr_log"; then
            if [[ -s "$temporary" ]]; then
                if mv -fT -- "$temporary" "$final" 2>> "$stderr_log"; then
                    temporary=''
                    if [[ ! -s "$stderr_log" ]]; then
                        rm -f -- "$stderr_log"
                    fi
                    (( successes += 1 ))
                    printf 'SUCCESS %s: %s\n' "$id" "$final" >&2
                    continue
                fi
            else
                printf 'Command succeeded but report was empty.\n' >> "$stderr_log"
            fi
        fi
    else
        printf 'Could not create temporary report.\n' > "$stderr_log"
    fi
    cleanup
    temporary=''
    (( failures += 1 ))
    if "$previous"; then
        printf 'FAILED %s: previous result preserved; stderr: %s\n' "$id" "$stderr_log" >&2
    else
        printf 'FAILED %s: no report published; stderr: %s\n' "$id" "$stderr_log" >&2
    fi
done
printf 'Summary: %s succeeded, %s failed.\n' "$successes" "$failures" >&2
(( failures == 0 ))
