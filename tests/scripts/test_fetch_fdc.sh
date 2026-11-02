#!/usr/bin/env bash
# Tests for scripts/fetch-fdc.sh (offline): a stub curl serves a zip built here; no real network call.
. "$(dirname "$0")/lib.sh"
REAL_PATH="$PATH"   # new_sandbox puts a stub git first on PATH
SCRIPT="$ROOT/scripts/fetch-fdc.sh"
URL="https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip"

# mkzip OUT LAYOUT: layout is "sub" (files under a directory), "flat", "missing" (no food_portion.csv),
# "evil" (also holds ../evil.csv), or "abs" (also holds an absolute path).
mkzip() {
  python3 - "$1" "$2" <<'PY'
import sys, zipfile
out, layout = sys.argv[1], sys.argv[2]
prefix = "FoodData_Central_sr_legacy_food_csv_2018-04/" if layout in ("sub", "evil", "abs") else ""
names = ["food.csv", "food_nutrient.csv", "food_portion.csv"]
if layout == "missing":
    names.remove("food_portion.csv")
with zipfile.ZipFile(out, "w") as z:
    for n in names:
        z.writestr(prefix + n, "id\n1\n")
    if layout == "evil":
        z.writestr("../evil.csv", "x")
    if layout == "abs":
        z.writestr("/tmp/evil-abs.csv", "x")
PY
}

# sandbox_curl: a curl stub that logs its arguments and copies $STUB_DIR/served to the -o target.
sandbox_curl() {
  : > "$T/curl.log"
  cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_DIR/curl.log"
[ -n "${STUB_CURL_FAIL:-}" ] && exit 22
out=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do [ "${args[$i]}" = "-o" ] && out="${args[$((i + 1))]}"; done
[ -n "$out" ] && cp "$STUB_DIR/served" "$out"
exit 0
STUB
  chmod +x "$T/bin/curl"
  mkdir -p "$T/work"
}
run() { (cd "$T/work" && bash "$SCRIPT" "$@" > "$T/out" 2> "$T/err"); rc=$?; }

echo "fetch-fdc: a zip with the files in a directory is unpacked and the directory is printed"
new_sandbox; sandbox_curl; mkzip "$T/served" sub
run
assert_eq "$rc" "0" "exits 0"
dir="$(tail -n 1 "$T/out")"
assert_eq "$dir" "$T/work/.agent-tmp/fdc/FoodData_Central_sr_legacy_food_csv_2018-04" "the last stdout line is the directory holding the files"
for f in food.csv food_nutrient.csv food_portion.csv; do
  [ -f "$dir/$f" ] && ok "$f is there" || bad "$f is there"
done
assert_eq "$(wc -l < "$T/out" | tr -d ' ')" "1" "stdout holds only that one line"
assert_count "$T/curl.log" '^curl ' 1 "curl ran once"
assert_grep "$T/curl.log" "$URL" "with the official URL"
assert_grep "$T/curl.log" '--proto =https' "and accepts only https"
assert_grep "$T/curl.log" '--proto-redir =https' "including redirects"
assert_grep "$T/err" '[0-9a-f]{64}' "the SHA-256 of the zip is reported on stderr"

echo "fetch-fdc: a zip with the files at the top level works too"
new_sandbox; sandbox_curl; mkzip "$T/served" flat
run
assert_eq "$rc" "0" "exits 0"
assert_eq "$(tail -n 1 "$T/out")" "$T/work/.agent-tmp/fdc" "the directory is the output directory itself"

echo "fetch-fdc: AGENT_TMP_DIR moves the output"
new_sandbox; sandbox_curl; mkzip "$T/served" flat
(cd "$T/work" && AGENT_TMP_DIR="$T/elsewhere" bash "$SCRIPT" > "$T/out" 2> "$T/err"); rc=$?
assert_eq "$rc" "0" "exits 0"
assert_eq "$(tail -n 1 "$T/out")" "$T/elsewhere/fdc" "the files land under AGENT_TMP_DIR"

echo "fetch-fdc: a second run downloads nothing"
new_sandbox; sandbox_curl; mkzip "$T/served" sub
run; run
assert_eq "$rc" "0" "exits 0"
assert_count "$T/curl.log" '^curl ' 1 "curl ran only for the first run"
assert_eq "$(tail -n 1 "$T/out")" "$T/work/.agent-tmp/fdc/FoodData_Central_sr_legacy_food_csv_2018-04" "and the same directory is printed"

echo "fetch-fdc: failures are loud and leave no output directory"
new_sandbox; sandbox_curl; mkzip "$T/served" sub
STUB_CURL_FAIL=1 run
if [ "$rc" -ne 0 ]; then ok "a failed download exits non-zero"; else bad "a failed download exits non-zero"; fi
assert_grep "$T/err" 'download' "and says the download failed"
[ ! -e "$T/work/.agent-tmp/fdc" ] && ok "and leaves no directory" || bad "and leaves no directory"
new_sandbox; sandbox_curl; : > "$T/served"
run
if [ "$rc" -ne 0 ]; then ok "an empty download exits non-zero"; else bad "an empty download exits non-zero"; fi
[ ! -e "$T/work/.agent-tmp/fdc" ] && ok "and leaves no directory" || bad "and leaves no directory"
new_sandbox; sandbox_curl; echo "<html>not a zip</html>" > "$T/served"
run
if [ "$rc" -ne 0 ]; then ok "a file that is not a zip exits non-zero"; else bad "a file that is not a zip exits non-zero"; fi
assert_grep "$T/err" 'zip' "and names the problem"
[ ! -e "$T/work/.agent-tmp/fdc" ] && ok "and leaves no directory" || bad "and leaves no directory"
new_sandbox; sandbox_curl; mkzip "$T/served" missing
run
if [ "$rc" -ne 0 ]; then ok "a zip without food_portion.csv exits non-zero"; else bad "a zip without food_portion.csv exits non-zero"; fi
assert_grep "$T/err" 'food_portion.csv' "and names the missing file"
[ ! -e "$T/work/.agent-tmp/fdc" ] && ok "and leaves no directory" || bad "and leaves no directory"
new_sandbox; sandbox_curl; mkzip "$T/served" evil
run
if [ "$rc" -ne 0 ]; then ok "a zip with a .. entry exits non-zero"; else bad "a zip with a .. entry exits non-zero"; fi
[ ! -e "$T/evil.csv" ] && [ ! -e "$T/work/evil.csv" ] && ok "and nothing was written outside the directory" || bad "and nothing was written outside the directory"
[ ! -e "$T/work/.agent-tmp/fdc" ] && ok "and leaves no directory" || bad "and leaves no directory"
new_sandbox; sandbox_curl; mkzip "$T/served" abs
run
if [ "$rc" -ne 0 ]; then ok "a zip with an absolute path exits non-zero"; else bad "a zip with an absolute path exits non-zero"; fi
[ ! -e /tmp/evil-abs.csv ] && ok "and nothing was written to the absolute path" || bad "and nothing was written to the absolute path"

echo "fetch-fdc: it takes no arguments"
new_sandbox; sandbox_curl; mkzip "$T/served" sub
run --url http://example.com/x.zip
assert_eq "$rc" "2" "an argument is a usage error (exit 2)"
assert_count "$T/curl.log" '^curl ' 0 "and curl is never called"

echo "fetch-fdc: the script is committed executable, and a change to it is high risk"
mode="$(cd "$ROOT" && PATH="$REAL_PATH" git ls-files -s scripts/fetch-fdc.sh | cut -d' ' -f1)"
assert_eq "$mode" "100755" "scripts/fetch-fdc.sh is committed executable"
assert_eq "$(printf 'scripts/fetch-fdc.sh\n' | bash "$ROOT/scripts/classify-risk.sh")" "high" "a change to the script is high risk"

finish
