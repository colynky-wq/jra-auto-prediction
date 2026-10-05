"""
scrape_and_update.py
JRA競馬予測システム - 当日レース結果の取得・pkl更新・GitHub反映スクリプト

【使い方】
Google Colab 上で実行してください（Google Driveのマウントが必要です）。
開催があった日の翌日以降に実行し、history_data.pkl を最新化します。

【前提】
- Google Driveに /MyDrive/code_output/jra_data/scrape_gap/history_data.pkl が存在すること
- ColabのシークレットにGITHUB_TOKEN（Contents: Read and write権限）が設定されていること

【カレンダーについて】
TOKYO_2026_SCHEDULE は東京競馬の開催日程（JRA公式発表）を手動で登録したものです。
新しい開催が発表されたら、ここに追記してください。
現状、東京以外の競馬場（京都など）には未対応です。

最終更新: 2026-10-05
"""
import os, re
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from google.colab import userdata, drive

try:
    drive.mount('/content/drive')
except Exception:
    pass

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}

WORK = "/content/drive/MyDrive/code_output/jra_data/scrape_gap"
pkl_path = f"{WORK}/history_data.pkl"

# 対象日：通常は実行日（必要なら手動で上書き可能）
TARGET_DATE = datetime.now().strftime('%Y-%m-%d')

TOKYO_2026_SCHEDULE = {
    "2026-10-03": 1, "2026-10-04": 2,
    "2026-10-10": 3, "2026-10-11": 4, "2026-10-12": 5,
    "2026-10-17": 6, "2026-10-18": 7,
    # 10/24, 11/1 は4回開催内だが日目未確認。わかり次第追加
}
TOKYO_MEETING_NUMBER = "04"
VENUE_CODE_TOKYO = "05"

JOCKEY_NAME_FIX = {
    "05339": "Ｃ．ルメール",
}

def resolve_jockey_name(jid, fallback):
    return JOCKEY_NAME_FIX.get(jid, fallback)

def get_race_ids(date_str):
    day_num = TOKYO_2026_SCHEDULE.get(date_str)
    if day_num is None:
        return []
    year = date_str[:4]
    return [f"{year}{VENUE_CODE_TOKYO}{TOKYO_MEETING_NUMBER}{str(day_num).zfill(2)}{str(r).zfill(2)}" for r in range(1, 13)]

def parse_race(race_id):
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}&rf=race_list"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.content, 'html.parser')
        race_name_tag = soup.find('h1', class_='RaceName')
        race_name = race_name_tag.text.strip() if race_name_tag else "Unknown"
        result_table = soup.find('table', id='All_Result_Table')
        if not result_table:
            return None
        rows = result_table.find_all('tr')[1:]
        results = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 15:
                continue
            jockey_link = cols[6].find('a')
            jockey_id = None
            if jockey_link and jockey_link.get('href'):
                m_id = re.search(r'/jockey/(?:result/recent/)?(\w+)/', jockey_link['href'])
                if m_id:
                    jockey_id = m_id.group(1)
            jockey_name = resolve_jockey_name(jockey_id, cols[6].text.strip())
            affil_text = cols[13].get_text(separator="\n", strip=True)
            affil_parts = [p for p in affil_text.split('\n') if p.strip()]
            affiliation = affil_parts[0] if affil_parts else ''
            trainer = affil_parts[1] if len(affil_parts) > 1 else ''
            weight_text = cols[14].text.strip()
            m = re.match(r'(\d+)\(([+-]?\d+)\)', weight_text)
            horse_weight, weight_diff = (m.group(1), m.group(2)) if m else (weight_text, '')
            results.append({
                'レースID': race_id, 'レース名': race_name,
                '着順': cols[0].text.strip(), '枠': cols[1].text.strip(),
                '馬番': cols[2].text.strip(), '馬名': cols[3].text.strip(),
                '性齢': cols[4].text.strip(), '斤量': cols[5].text.strip(),
                '騎手': jockey_name, '騎手ID': jockey_id or '',
                'タイム': cols[7].text.strip(), '着差': cols[8].text.strip(),
                '人気': cols[9].text.strip(), '単勝オッズ': cols[10].text.strip(),
                '後3F': cols[11].text.strip(), 'コーナー通過順': cols[12].text.strip(),
                '所属': affiliation, '調教師': trainer,
                '馬体重': horse_weight, '増減': weight_diff,
            })
        return results
    except Exception as e:
        print(f"  ⚠ {race_id} 取得失敗: {str(e)[:80]}")
        return None


print("="*60)
print(f"本日のレース取得＆更新：{TARGET_DATE}")
print("="*60)

print(f"\n[1/4] Drive上のデータを読み込み中...")
hist_df = pd.read_pickle(pkl_path)
print(f"  ✓ 行数: {len(hist_df)}行")

print(f"\n[2/4] {TARGET_DATE} のレースをスクレイピング中...")
race_ids = get_race_ids(TARGET_DATE)

if not race_ids:
    print("  本日は東京開催日ではありません。処理を終了します。")
else:
    all_results = []
    for rid in race_ids:
        results = parse_race(rid)
        if results:
            all_results.extend(results)
            print(f"  ✓ {rid} → {len(results)}頭")
        else:
            print(f"  ❌ {rid} → 取得失敗（未発走の可能性）")

    if all_results:
        print(f"\n[3/4] マージしてDriveに保存中...")
        new_df = pd.DataFrame(all_results)
        hist_df = hist_df[~hist_df['レースID'].isin(race_ids)]
        hist_df = pd.concat([hist_df, new_df], ignore_index=True)
        hist_df.to_pickle(pkl_path)
        print(f"  ✓ 更新後: {len(hist_df)}行 → Driveに保存完了")

        print(f"\n[4/4] GitHubへアップロード中...")
        try:
            GITHUB_TOKEN = userdata.get('GITHUB_TOKEN')
        except Exception:
            GITHUB_TOKEN = None

        if not GITHUB_TOKEN:
            print("  ❌ GITHUB_TOKEN が設定されていません")
        else:
            OWNER, REPO, TAG, ASSET_NAME = "colynky-wq", "jra-auto-prediction", "v1.0", "history_data.pkl"
            api_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

            resp = requests.get(f"https://api.github.com/repos/{OWNER}/{REPO}/releases/tags/{TAG}", headers=api_headers)
            release_data = resp.json()
            release_id = release_data['id']

            existing = next((a for a in release_data['assets'] if a['name'] == ASSET_NAME), None)
            if existing:
                d = requests.delete(f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/{existing['id']}", headers=api_headers)
                print(f"  （既存アセット削除: {'成功' if d.status_code == 204 else '失敗:' + str(d.status_code)}）")

            upload_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/octet-stream"}
            with open(pkl_path, 'rb') as f:
                up = requests.post(
                    f"https://uploads.github.com/repos/{OWNER}/{REPO}/releases/{release_id}/assets?name={ASSET_NAME}",
                    headers=upload_headers, data=f
                )

            if up.status_code == 201:
                print(f"  ✅ アップロード成功！\n  {up.json()['browser_download_url']}")
            else:
                print(f"  ❌ アップロード失敗: {up.status_code}\n  {up.text[:300]}")
    else:
        print("\n⚠ 取得データなし（レースがまだ発走していない可能性）")

print("\n完了！")
