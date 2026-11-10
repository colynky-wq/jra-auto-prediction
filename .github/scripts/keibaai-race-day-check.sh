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

# JRA's published 2026 annual schedule includes four weekday holiday
# sessions in addition to the normal Saturday/Sunday programme.
# Source: https://www.jra.go.jp/keiba/program/2026/pdf/kaisainittei.pdf
is_2026_weekday_race_date() {
  case "$date8" in
    20260112|20260921|20261012|20261123) return 0 ;;
    *) return 1 ;;
  esac
}

# If live calendar providers are blocked, permit a provisional live schedule
# probe only on a published JRA race day. The live monitor still fetches the
# actual schedule and can publish only within its unchanged formal T-10 window.
allow_scheduled_day_probe() {
  local reason="$1"
  local weekday
  weekday=$(date -d "${date8:0:4}-${date8:4:2}-${date8:6:2}" '+%u')
  if [ "$weekday" -ge 6 ] || is_2026_weekday_race_date; then
    emit_result true
    echo "::warning::${date8}の開催判定は未確認（${reason}）。公表済み開催日候補のため実レース一覧を確認する本番監視だけを許可します。開催・保存成功の証拠にはしません。"
    return 0
  fi
  return 1
}

# The official 2026 annual JRA schedule has no ordinary weekday meetings
# outside the four holiday dates above. This avoids turning a known non-race
# weekday into a failed production run when both live calendar endpoints
# reject the runner. Other years remain fail-closed until their official
# annual schedule is explicitly added.
confirm_2026_non_race_weekday() {
  local weekday
  weekday=$(date -d "${date8:0:4}-${date8:4:2}-${date8:6:2}" '+%u')
  if [ "${date8:0:4}" = "2026" ] && [ "$weekday" -le 5 ] && ! is_2026_weekday_race_date; then
    emit_result false
    echo "No JRA meeting scheduled on weekday ${date8} in the published 2026 annual schedule."
    return 0
  fi
  return 1
}

handle_ambiguous_calendar() {
  local reason="$1"
  if allow_scheduled_day_probe "$reason"; then return 0; fi
  if confirm_2026_non_race_weekday; then return 0; fi
  echo "::error::${date8}の開催確認が不確実です。開催なしとは扱わず停止します（${reason}）。"
  return 1
}

# Primary source: same race-list source used by the live monitor. A positive
# race-ID parse is enough to start. Zero IDs are cross-checked against JRA.
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
    echo "::warning::${date8}のnetkeibaページからレースIDを抽出できませんでした。JRA公式で再確認します。"
  else
    echo "::warning::${date8}のnetkeiba開催一覧が空でした。JRA公式で再確認します。"
  fi
else
  echo "::warning::${date8}のnetkeiba開催確認に失敗しました。JRA公式へフォールバックします。"
fi

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
  handle_ambiguous_calendar "JRA通信失敗" && exit 0
  exit 1
fi

if [ "$jra_code" = '200' ]; then
  if grep -q '競馬番組' "$jra_file" && grep -Eq '1レース|1R' "$jra_file"; then
    emit_result true
    echo "JRA race day detected via official JRA fallback: ${date8}"
    exit 0
  fi
  handle_ambiguous_calendar "JRA番組解析が不確実" && exit 0
  exit 1
fi

if [ "$jra_code" = '404' ]; then
  emit_result false
  echo "No official JRA daily programme for ${date8}; no-race day confirmed."
  exit 0
fi

handle_ambiguous_calendar "JRA HTTP ${jra_code}" && exit 0
exit 1
