#!/usr/bin/env bash
set -euo pipefail

DENYLIST="${PRIVATE_DENYLIST:-.private-denylist}"
MODE="${1:-tree}"

blocked_path_regex='(^|/)location_registry(\.local|\.private)?\.json$'
allowed_registry_regex='(^|/)location_registry\.example\.json$'

check_file_list_for_private_registry() {
  local failed=0
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [[ "$file" =~ $blocked_path_regex ]] && ! [[ "$file" =~ $allowed_registry_regex ]]; then
      echo "Refusing private weather registry path: $file" >&2
      failed=1
    fi
  done
  return "$failed"
}

scan_files_for_denylist() {
  [ -f "$DENYLIST" ] || return 0
  local files_file="$1"
  local failed=0
  while IFS= read -r pattern || [ -n "$pattern" ]; do
    pattern="${pattern%$'\r'}"
    [ -n "$pattern" ] || continue
    case "$pattern" in \#*) continue ;; esac
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      [ -f "$file" ] || continue
      case "$file" in
        "$DENYLIST"|./"$DENYLIST"|*/location_registry.json|*/location_registry.local.json|*/location_registry.private.json) continue ;;
      esac
      if grep -Fqi -- "$pattern" "$file"; then
        echo "Potential private weather data in $file; matched local denylist entry." >&2
        failed=1
      fi
    done < "$files_file"
  done < "$DENYLIST"
  return "$failed"
}

tmp_files="$(mktemp)"
trap 'rm -f "$tmp_files"' EXIT

case "$MODE" in
  staged)
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      git diff --cached --name-only --diff-filter=ACMRT > "$tmp_files"
    else
      : > "$tmp_files"
    fi
    ;;
  tree)
    find . \
      -path './.git' -prune -o \
      -path './weather/location_registry.json' -prune -o \
      -path './weather/location_registry.local.json' -prune -o \
      -path './weather/location_registry.private.json' -prune -o \
      -type f -print | sed 's#^./##' > "$tmp_files"
    ;;
  *)
    echo "Usage: $0 [tree|staged]" >&2
    exit 2
    ;;
esac

check_file_list_for_private_registry < "$tmp_files"
scan_files_for_denylist "$tmp_files"

echo "Private weather data check passed ($MODE)."
