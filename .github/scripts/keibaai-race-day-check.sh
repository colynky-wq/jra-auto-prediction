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

# Primary source: same race-list source used by the live monitor.  A successful,
# non-empty response with zero race IDs is treated as a genuine no-race day.
if curl -fsSL --retry 2 --retry-all-errors --connect-timeout 5 --max-time 20 \
  -A 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36' \
  "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=${date8}" -o "$netkeiba_file"; then
  if ! LC_ALL=C grep -q '[^[:space:]]' "$netkeiba_file"; then
    echo "::warning::${date8}のnetkeiba開催一覧が空でした。JRA公式で再確認します。"
  else
    ids=$(grep -oE 'race_id=[0-9]{12}' "$netkeiba_file" || true)
    count=$(printf '%s\n' "$ids" | sed '/^$/d' | sort -u | wc -l | tr -d ' ')
    echo "target_date=${date8}" >> "$GITHUB_OUTPUT"
    if [ "${count:-0}" -gt 0 ]; then
      echo 'has_races=true' >> "$GITHUB_OUTPUT"
      echo "JRA race day detected via netkeiba: ${date8}, races=${count}"
    else
      echo 'has_races=false' >> "$GITHUB_OUTPUT"
      echo "No JRA races detected for ${date8}; exiting cheaply."
    fi
    exit 0
  fi
else
  echo "::warning::${date8}のnetkeiba開催確認に失敗しました。JRA公式へフォールバックします。"
fi

# Secondary source: official JRA calendar page.  This path is only used when the
# primary response is unavailable/empty; communication failure is never silently
# converted into 'no races'.
year="${date8:0:4}"
month="${date8:4:2}"
day="${date8:6:2}"
jra_url="https://www.jra.go.jp/keiba/calendar${year}/${year}/${month}/${month}${day}.html"
if curl -fsSL --retry 2 --retry-all-errors --connect-timeout 5 --max-time 20 \
  -A 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36' \
  "$jra_url" -o "$jra_file"; then
  if grep -q '競馬番組' "$jra_file" && grep -Eq '1レース|1R' "$jra_file"; then
    echo "target_date=${date8}" >> "$GITHUB_OUTPUT"
    echo 'has_races=true' >> "$GITHUB_OUTPUT"
    echo "JRA race day detected via official JRA fallback: ${date8}"
    exit 0
  fi
fi

echo "::error::${date8}の開催有無を確認できませんでした。通信・解析失敗を開催なしと扱わず停止します。"
exit 1
