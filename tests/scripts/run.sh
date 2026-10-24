#!/usr/bin/env bash
# Runs every tests/scripts/test_*.sh. Offline: gh, claude and date are stubbed.
# Usage: bash tests/scripts/run.sh
cd "$(dirname "$0")" || exit 1
fail=0
for t in test_*.sh; do
  echo "== $t"
  bash "$t" || fail=1
done
if [ "$fail" -eq 0 ]; then echo "ALL SCRIPT TESTS PASSED"; else echo "SCRIPT TESTS FAILED"; fi
exit "$fail"
