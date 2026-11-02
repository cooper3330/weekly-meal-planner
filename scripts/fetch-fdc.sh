#!/usr/bin/env bash
# Downloads the USDA FoodData Central "SR Legacy" CSV files and unpacks them where the developer agent
# can read them. Prints the directory that holds food.csv, food_nutrient.csv and food_portion.csv as the
# last line on stdout; everything else goes to stderr.
#
# Usage: scripts/fetch-fdc.sh        (no arguments)
#
# Why a script: ADR 0020 has the developer download SR Legacy once, by hand, and the converter
# (`python -m mealplanner.nutrition.build_catalog extract --fdc-dir DIR`) reads it. The agents run
# headless with a narrow command allowlist and no free-form curl, so this one fixed download is
# allowed instead. The URL is a constant here on purpose: an agent cannot point it elsewhere.
#
# The files land in ${AGENT_TMP_DIR:-.agent-tmp}/fdc (gitignored) and are never committed; only the
# converter's fdc_extract.csv is. A second run finds the files and downloads nothing.
# Requires: curl, unzip. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

URL="https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip"
FILES="food.csv food_nutrient.csv food_portion.csv"

if [ $# -ne 0 ]; then
  echo "usage: scripts/fetch-fdc.sh   (takes no arguments)" >&2
  exit 2
fi
for cmd in curl unzip; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done

base="${AGENT_TMP_DIR:-.agent-tmp}"
mkdir -p "$base"
base="$(cd "$base" && pwd)"
root="$base/fdc"

# find_dir DIR: prints DIR itself, or its first sub-directory, that holds all three files.
find_dir() {
  local d f ok
  for d in "$1" "$1"/*/; do
    [ -d "$d" ] || continue
    ok=1
    for f in $FILES; do [ -f "$d/$f" ] || ok=0; done
    if [ "$ok" -eq 1 ]; then echo "${d%/}"; return 0; fi
  done
  return 1
}

if [ -d "$root" ] && found="$(find_dir "$root")"; then
  echo "SR Legacy is already in $found; nothing to download" >&2
  echo "$found"
  exit 0
fi

work="$(mktemp -d "$base/.fetch-fdc.XXXXXX")"
trap 'rm -rf "$work"' EXIT
fail() { echo "fetch-fdc: $1" >&2; exit 1; }

echo "downloading $URL" >&2
curl -fsS --retry 3 --connect-timeout 30 --max-time 900 \
  --proto '=https' --proto-redir '=https' -L -o "$work/fdc.zip" "$URL" \
  || fail "the download failed (is fdc.nal.usda.gov reachable from this runner?)"
[ -s "$work/fdc.zip" ] || fail "the download is empty"

entries="$(unzip -Z1 "$work/fdc.zip" 2>/dev/null)" || fail "the download is not a valid zip file"
while IFS= read -r name; do
  case "$name" in
    /*|..|../*|*/../*|*/..) fail "the zip holds an unsafe path ($name); not unpacking it" ;;
  esac
done <<<"$entries"

mkdir "$work/x"
unzip -q "$work/fdc.zip" -d "$work/x" || fail "could not unpack the zip"
dir="$(find_dir "$work/x")" || {
  missing=""
  for f in $FILES; do
    find "$work/x" -name "$f" -type f 2>/dev/null | grep -q . || missing="$missing $f"
  done
  fail "the zip does not hold the expected CSV files (missing:${missing:- unknown})"
}

if command -v sha256sum >/dev/null 2>&1; then sum="$(sha256sum "$work/fdc.zip")"; else sum="$(shasum -a 256 "$work/fdc.zip")"; fi
echo "sha256 ${sum%% *}  FoodData_Central_sr_legacy_food_csv_2018-04.zip" >&2

rm -rf "$root"
mkdir -p "$root"
if [ "$dir" = "$work/x" ]; then
  mv "$work"/x/* "$root"/
  final="$root"
else
  mv "$dir" "$root/"
  final="$root/$(basename "$dir")"
fi
echo "unpacked SR Legacy into $final" >&2
echo "$final"
