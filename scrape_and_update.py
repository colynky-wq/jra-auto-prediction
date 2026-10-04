import pandas as pd
import pickle
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import os

print("=" * 80)
print("当日レース結果スクレイピング＆pkl更新")
print("=" * 80)

today = datetime.now()
today_str = today.strftime("%Y%m%d")
today_jp = today.strftime("%Y年%m月%d日")
print(f"\n【対象日】{today_jp}")

# ① 既存の pkl を読み込み
print("\n[1/4] 既存データを読み込み中...")
try:
    hist = pickle.load(open('history_data.pkl', 'rb'))
    print(f"  ✓ 既存データ: {len(hist):,}行")
except Exception as e:
    print(f"  ✗ 読み込みエラー: {e}")
    exit(1)

# ② NetKeiba から本日のレース一覧を取得
print(f"\n[2/4] {today_jp}のレースを NetKeiba からスクレイピング中...")

try:
    # NetKeiba のレース一覧ページ（本日分）
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={today_str}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    response = requests.get(url, headers=headers, timeout=10)
    response.encoding = 'euc-jp'
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # レースID と馬情報を抽出
    race_tables = soup.find_all('table', class_='race_table_01')
    
    new_records = []
    
    for table in race_tables:
        # レースID の抽出
        race_link = table.find('a', href=True)
        if not race_link:
            continue
        
        href = race_link['href']
        # https://race.netkeiba.com/race/20261004010101/ → 20261004010101
        if '/race/' in href:
            race_id = href.split('/race/')[1].rstrip('/')
        else:
            continue
        
        # 馬情報を抽出
        rows = table.find_all('tr')[1:]  # ヘッダー行をスキップ
        
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 10:
                continue
            
            try:
                着順 = cells[0].text.strip()
                枠 = cells[1].text.strip()
                馬番 = cells[2].text.strip()
                馬名 = cells[3].text.strip()
                性齢 = cells[4].text.strip()
                斤量 = cells[5].text.strip()
                騎手 = cells[6].text.strip()
                タイム = cells[7].text.strip()
                着差 = cells[8].text.strip()
                人気 = cells[9].text.strip()
                
                # 単勝オッズを取得（別途取得が必要な場合もある）
                単勝オッズ = "N/A"
                
                record = {
                    'レースID': race_id,
                    '着順': 着順,
                    '枠': 枠,
                    '馬番': 馬番,
                    '馬名': 馬名,
                    '性齢': 性齢,
                    '斤量': 斤量,
                    '騎手': 騎手,
                    'タイム': タイム,
                    '着差': 着差,
                    '人気': 人気,
                    '単勝オッズ': 単勝オッズ
                }
                new_records.append(record)
            except Exception as e:
                print(f"    ⚠ 行パース失敗: {e}")
                continue
    
    if len(new_records) == 0:
        print(f"  ⚠ {today_jp}のレース結果が見つかりません（レース未実施の可能性）")
    else:
        print(f"  ✓ {len(new_records):,}件のレース結果を取得")
        
        # ③ pkl に新規データを追加
        print(f"\n[3/4] pkl に追加中...")
        new_df = pd.DataFrame(new_records)
        hist = pd.concat([hist, new_df], ignore_index=True)
        print(f"  ✓ 更新後のデータ: {len(hist):,}行")
        
        # ④ 更新した pkl を保存
        print(f"\n[4/4] pkl を保存中...")
        with open('history_data.pkl', 'wb') as f:
            pickle.dump(hist, f)
        print(f"  ✓ 保存完了")

except requests.exceptions.RequestException as e:
    print(f"  ✗ ネットワークエラー: {e}")
except Exception as e:
    print(f"  ✗ スクレイピングエラー: {e}")

print("\n" + "=" * 80)
print("✓ スクレイピング＆更新完了！")
print("=" * 80)
