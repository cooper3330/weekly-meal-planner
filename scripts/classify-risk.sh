#!/usr/bin/env bash
# Classify a PR by risk. Reads newline-separated changed paths on stdin; prints `high` if any
# matches a rule in .github/risk-paths, else `low`. Offline: no network or gh calls.
# Fails closed (non-zero, prints nothing) if the rules file is missing or has no rules.
# Usage: classify-risk.sh [--rules FILE] < paths
set -u

rules_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.github/risk-paths"
if [ "${1:-}" = "--rules" ]; then
  [ $# -ge 2 ] || { echo "classify-risk: --rules needs a file" >&2; exit 2; }
  rules_file="$2"
fi
[ -f "$rules_file" ] || { echo "classify-risk: rules file not found: $rules_file" >&2; exit 2; }

rules=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$line" in ''|\#*) continue ;; esac
  [[ "$line" =~ ^[[:space:]]*$ ]] && continue
  rules+=("$line")
done < "$rules_file"
[ "${#rules[@]}" -gt 0 ] || { echo "classify-risk: rules file has no rules: $rules_file" >&2; exit 2; }

verdict=low
while IFS= read -r path || [ -n "$path" ]; do
  path="${path%$'\r'}"
  [ -n "$path" ] || continue
  for rule in "${rules[@]}"; do
    case "$rule" in
      */)    [[ "$path" == "$rule"* ]] && verdict=high ;;
      '**/'*) [ "${path##*/}" = "${rule#'**/'}" ] && verdict=high ;;
      *)     [ "$path" = "$rule" ] && verdict=high ;;
    esac
  done
  [ "$verdict" = high ] && break
done
echo "$verdict"
