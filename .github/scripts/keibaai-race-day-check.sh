#!/usr/bin/env bash
set -euo pipefail

date8="${1:-}"
if [[ ! "$date8" =~ ^[0-9]{8}$ ]] || ! date -d "${date8:0:4}-${date8:4:2}-${date8:6:2}" '+%Y%m%d' > /dev/null 2>&1; then
  echo '::error::開催確認の日付が不正です。'
  exit 2
fi

netkeiba_file=$(mktemp)
jra_file=$(mktemp)
trap 'rm -f "$netkeiba_file" "$jra_file"' EXIT

emit_result() {
  local has_races="$1"
  echo "target_date=${date8}" >> "$GITHUB_OUTPUT"
  echo "has_races=${has_races}" >> "$GITHUB_OUTPUT"
}

# Primary source: same race-list source used by the live monitor.  A positive
# race-ID parse is enough to start.  Zero IDs are NOT trusted as a no-race day,
# because an upstream HTML change can look exactly like zero races; official JRA
# is cross-checked before the monitor is allowed to stay off.
if curl -fsSL --retry 2 --retry-all-errors --connect-timeout 5 --max-time 20 \
  -A 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36' \
  "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=${date8}" -o "$netkeiba_file"; then
  if LC_ALL=C grep -q '[^[:space:]]' "$netkeiba_file"; then
    ids=$(grep -oE 'race_id=[0-9]{12}' "$netkeiba_file" || true)
    count=$(printf '%s\n' "$ids" | sed '/^$/d' | sort -u | wc -l | tr -d ' ')
    if [ "${count:-0}" -gt 0 ]; then
      emit_result true
      echo "JRA race day detected via netkeiba: ${date8}, races=${count}"
      exit 0
    fi
    echo "::warning::${date8}のnetkeibaページからレースIDを抽出できませんでした。HTML変更との区別のためJRA公式で再確認します。"
  else
    echo "::warning::${date8}のnetkeiba開催一覧が空でした。JRA公式で再確認します。"
  fi
else
  echo "::warning::${date8}のnetkeiba開催確認に失敗しました。JRA公式へフォールバックします。"
fi

# Secondary source: official JRA calendar page.  HTTP 404 is a verified absence
# of a daily programme and can safely mean no JRA races.  Any other transport or
# parse ambiguity fails closed instead of silently suppressing the live monitor.
year="${date8:0:4}"
month="${date8:4:2}"
day="${date8:6:2}"
jra_url="https://www.jra.go.jp/keiba/calendar${year}/${year}/${month}/${month}${day}.html"
set +e
jra_code=$(curl -sSL --retry 2 --retry-all-errors --connect-timeout 5 --max-time 20 \
  -A 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36' \
  -o "$jra_file" -w '%{http_code}' "$jra_url")
jra_status=$?
set -e

if [ "$jra_status" -ne 0 ]; then
  echo "::error::${date8}のJRA公式開催確認に通信失敗しました。開催なしとは扱いません。"
  exit 1
fi

if [ "$jra_code" = '200' ]; then
  if grep -q '競馬番組' "$jra_file" && grep -Eq '1レース|1R' "$jra_file"; then
    emit_result true
    echo "JRA race day detected via official JRA fallback: ${date8}"
    exit 0
  fi
  echo "::error::${date8}のJRA公式ページは取得できましたが競馬番組を確認できません。解析失敗として停止します。"
  exit 1
fi

if [ "$jra_code" = '404' ]; then
  emit_result false
  echo "No official JRA daily programme for ${date8}; no-race day confirmed."
  exit 0
fi

echo "::error::${date8}のJRA公式開催確認がHTTP ${jra_code}でした。開催なしとは扱わず停止します。"
exit 1
