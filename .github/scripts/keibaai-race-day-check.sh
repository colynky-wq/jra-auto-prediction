#!/usr/bin/env bash
set -euo pipefail
date8="${1:-}"
if [[ ! "$date8" =~ ^[0-9]{8}$ ]] || ! date -d "${date8:0:4}-${date8:4:2}-${date8:6:2}" '+%Y%m%d' > /dev/null 2>&1; then
  echo '::error::開催確認の日付が不正です。'
  exit 2
fi
html_file=$(mktemp)
trap 'rm -f "$html_file"' EXIT
if ! curl -fsSL --retry 2 --connect-timeout 5 --max-time 20 \
  -A 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36' \
  "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=${date8}" -o "$html_file"; then
  echo "::error::${date8}の開催確認に失敗しました。通信失敗を開催なしと扱わず、自動監視をエラー終了します。"
  exit 1
fi
if ! LC_ALL=C grep -q '[^[:space:]]' "$html_file"; then
  echo "::error::${date8}の開催一覧が空でした。開催の有無を確認できません。"
  exit 1
fi
ids=$(grep -oE 'race_id=[0-9]{12}' "$html_file" || true)
count=$(printf '%s\n' "$ids" | sed '/^$/d' | sort -u | wc -l | tr -d ' ')
echo "target_date=${date8}" >> "$GITHUB_OUTPUT"
if [ "${count:-0}" -gt 0 ]; then
  echo 'has_races=true' >> "$GITHUB_OUTPUT"
  echo "JRA race day detected: ${date8}, races=${count}"
else
  echo 'has_races=false' >> "$GITHUB_OUTPUT"
  echo "No JRA races detected for ${date8}; exiting cheaply."
fi
