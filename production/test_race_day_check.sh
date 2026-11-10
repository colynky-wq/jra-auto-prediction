#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/.github/scripts/keibaai-race-day-check.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"

cat > "$TMP/bin/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
out=""
writeout=""
url=""
while (($#)); do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) writeout="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [[ "$url" == *race_list_sub.html* ]]; then
  case "${NETKEIBA_MODE:-empty}" in
    positive) printf 'race_id=202611140101\nrace_id=202611140102\n' > "$out" ;;
    fail) exit 22 ;;
    empty) printf '<html>calendar</html>\n' > "$out" ;;
  esac
  exit 0
fi
printf '%s\n' '<html>blocked</html>' > "$out"
printf '%s' "${JRA_CODE:-403}"
exit 0
CURL
chmod +x "$TMP/bin/curl"

run_case() {
  local date8="$1" mode="$2" code="$3" expected_exit="$4" expected_races="$5"
  local output="$TMP/output" log="$TMP/log"
  : > "$output"
  set +e
  PATH="$TMP/bin:$PATH" GITHUB_OUTPUT="$output" NETKEIBA_MODE="$mode" JRA_CODE="$code" \
    bash "$CHECK" "$date8" >"$log" 2>&1
  local status=$?
  set -e
  if [ "$status" -ne "$expected_exit" ]; then
    cat "$log"
    echo "FAIL: $date8 exit=$status expected=$expected_exit" >&2
    exit 1
  fi
  if [ "$expected_exit" -eq 0 ]; then
    grep -q "has_races=$expected_races" "$output" || {
      cat "$output" "$log"
      echo "FAIL: $date8 expected has_races=$expected_races" >&2
      exit 1
    }
  fi
  echo "PASS $date8 mode=$mode JRA=$code exit=$status has_races=$expected_races"
}

# A blocked JRA endpoint on a normal 2026 weekday must be a clean no-race skip.
run_case 20261110 empty 403 0 false
# Published Monday holiday meeting must still enter provisional live schedule probe.
run_case 20261123 empty 403 0 true
# Normal Saturday with blocked calendar sources remains a provisional probe.
run_case 20261114 empty 403 0 true
# A positive race-ID parse always takes precedence.
run_case 20261114 positive 403 0 true
# Unknown-year weekday ambiguity must remain fail-closed.
run_case 20271109 empty 403 1 none

echo "RACE_DAY_CHECK_TESTS_OK"
