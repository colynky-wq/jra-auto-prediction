import os, re, time, random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import pandas as pd
import pickle
from datetime import datetime

print("=" * 80)
print("当日レース結果スクレイピング＆pkl更新（Selenium版）")
print("=" * 80)

today = datetime.now()
today_str = today.strftime("%Y%m%d")
today_jp = today.strftime("%Y年%m月%d日")
print(f"\n【対象日】{today_jp}")

# ① links_2026.txt を読み込み
print("\n[1/6] links_2026.txt を読み込み中...")
try:
    with open('links_2026.txt', 'r', encoding='utf-8') as f:
        links = f.read()
    race_ids = [m for m in re.findall(r'race_id=(\d{12})', links)]
    print(f"  ✓ 全レースID数: {len(race_ids)}")
except FileNotFoundError:
    print("  ✗ links_2026.txt が見つかりません")
    exit(1)

# ② 本日のレースID抽出
today_races = [rid for rid in race_ids if rid.startswith(today_str)]
print(f"\n[2/6] 本日のレースID抽出")
print(f"  ✓ 本日のレース数: {len(today_races)}")

if len(today_races) == 0:
    print(f"  ⚠ {today_jp}にはレースがありません")
    try:
        hist = pickle.load(open('history_data.pkl', 'rb'))
        print(f"  既存データ: {len(hist):,}行")
    except:
        print("  ✗ 既存 pkl も見つかりません")
    exit(0)

# ③ Selenium セットアップ
print(f"\n[3/6] Selenium (Chrome) をセットアップ中...")
options = webdriver.ChromeOptions()
options.add_argument('--headless')  # ヘッドレスモード
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--disable-gpu')
options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15')

try:
    driver = webdriver.Chrome(options=options)
    print(f"  ✓ Chrome ドライバ起動成功")
except Exception as e:
    print(f"  ✗ Chrome ドライバ起動失敗: {e}")
    exit(1)

# ④ スクレイピング関数
SLEEP = (0.5, 1.0)
URL = "https://race.netkeiba.com/race/result.html?race_id={}&rf=race_list"

def _t(x):
    return re.sub(r"\s+", " ", x.get_text(" ", strip=True)) if x is not None else ""

def _id(a, pat):
    m = re.search(pat, a.get("href", "")) if a else None
    return m.group(1) if m else ""

def fetch_with_selenium(race_id, driver):
    """Selenium で JavaScript を実行してから HTML を取得"""
    try:
        driver.get(URL.format(race_id))
        
        # テーブルが読み込まれるまで待機（最大10秒）
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.ID, "All_Result_Table"))
        )
        
        time.sleep(1)  # 追加の待機
        html = driver.page_source
        return html, "ok"
    except Exception as e:
        return None, str(e)[:80]

def parse_race(html, race_id):
    s = BeautifulSoup(html, "html.parser")
    tbl = s.select_one("table#All_Result_Table")
    name = s.select_one("h1.RaceName")
    rows = tbl.select("tbody tr.HorseList") if tbl else []
    
    if not tbl or not rows or name is None or not _t(name):
        return "none", []
    
    horses = []
    for tr in rows:
        td = tr.find_all("td")
        cls = lambda c: tr.select_one("td." + c)
        times = tr.select("td.Time")
        w = cls("Weight")
        wt = _t(w)
        mw = re.match(r"(\d+)\s*\(([+-]?\d+)\)", wt)
        hn = tr.select_one("span.Horse_Name a")
        jk = cls("Jockey")
        trn = cls("Trainer")
        info = tr.select("td.Horse_Info")
        
        horse_record = {
            "レースID": race_id,
            "着順": _t(tr.select_one("div.Rank")),
            "枠": _t(tr.select_one("td[class*=Waku]")),
            "馬番": _t(tr.select("td.Num")[-1]) if tr.select("td.Num") else "",
            "馬名": hn.get("title", "") if hn else "",
            "馬ID": _id(hn, r"/horse/(\w+)"),
            "性齢": _t(info[-1]) if info else "",
            "斤量": _t(tr.select_one("span.JockeyWeight")),
            "騎手": _t(jk),
            "騎手ID": _id(jk.find("a") if jk else None, r"/jockey/result/recent/(\w+)"),
            "タイム": _t(times[0]) if len(times) > 0 else "",
            "着差": _t(times[1]) if len(times) > 1 else "",
            "後3F": _t(times[2]) if len(times) > 2 else "",
            "人気": _t(tr.select_one("span.OddsPeople")),
            "単勝オッズ": _t(tr.select("td.Odds")[-1]) if tr.select("td.Odds") else "",
            "コーナー通過順": _t(cls("PassageRate")),
            "所属": _t(trn.select_one("span[class^=Label]")) if trn else "",
            "調教師": (trn.find("a").get("title", "") if trn and trn.find("a") else ""),
            "調教師ID": _id(trn.find("a") if trn else None, r"/trainer/result/recent/(\w+)"),
            "馬体重": mw.group(1) if mw else "",
            "増減": mw.group(2) if mw else ""
        }
        horses.append(horse_record)
    
    return "ok", horses

# ⑤ スクレイピング実行
print(f"\n[4/6] NetKeiba からスクレイピング中（Selenium使用）...")
all_horses = []
success_count = 0

for i, race_id in enumerate(today_races):
    html, st = fetch_with_selenium(race_id, driver)
    time.sleep(random.uniform(*SLEEP))
    
    if html is None:
        print(f"  ✗ {race_id} 取得失敗: {st}")
        continue
    
    status, horses = parse_race(html, race_id)
    if status == "ok":
        all_horses.extend(horses)
        success_count += 1
        if (i + 1) % 20 == 0:
            print(f"  進捗: {i+1}/{len(today_races)} ({success_count}成功)")
    else:
        print(f"  ⚠ {race_id} 解析失敗")

driver.quit()  # ブラウザを閉じる

print(f"  成功: {success_count}/{len(today_races)} レース")
print(f"  取得馬数: {len(all_horses)}")

if len(all_horses) == 0:
    print("  ⚠ 本日のレース結果が取得できませんでした")
    exit(0)

# ⑥ 既存 pkl と統合
print(f"\n[5/6] 既存データと統合中...")
try:
    hist = pickle.load(open('history_data.pkl', 'rb'))
    print(f"  既存データ: {len(hist):,}行")
except:
    hist = pd.DataFrame()
    print(f"  既存データ: なし")

new_df = pd.DataFrame(all_horses)
hist = pd.concat([hist, new_df], ignore_index=True)
print(f"  統合後: {len(hist):,}行")

# ⑦ pkl に保存
print(f"\n[6/6] pkl を保存中...")
with open('history_data.pkl', 'wb') as f:
    pickle.dump(hist, f)
print(f"  ✓ 保存完了")

print("\n" + "=" * 80)
print("✓ スクレイピング＆更新完了！")
print("=" * 80)
