import os, re, time, random
import requests
from bs4 import BeautifulSoup
import pandas as pd
import pickle
from datetime import datetime

print("=" * 80)
print("当日レース結果スクレイピング＆pkl更新")
print("=" * 80)

today = datetime.now()
today_str = today.strftime("%Y%m%d")
today_jp = today.strftime("%Y年%m月%d日")
print(f"\n【対象日】{today_jp}")

# ① links_2026.txt を読み込み
print("\n[1/5] links_2026.txt を読み込み中...")
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
print(f"\n[2/5] 本日のレースID抽出")
print(f"  ✓ 本日のレース数: {len(today_races)}")

if len(today_races) == 0:
    print(f"  ⚠ {today_jp}にはレースがありません")
    # 既存の pkl を読み込むだけ
    try:
        hist = pickle.load(open('history_data.pkl', 'rb'))
        print(f"  既存データ: {len(hist):,}行")
    except:
        print("  ✗ 既存 pkl も見つかりません")
    exit(0)

print(f"  対象レースID: {today_races[:3]}")

# ③ スクレイピング用の関数（既存コードから）
SLEEP = (0.5, 1.0)
URL = "https://race.netkeiba.com/race/result.html?race_id={}&rf=race_list"
HEAD = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}

def _t(x):
    return re.sub(r"\s+", " ", x.get_text(" ", strip=True)) if x is not None else ""

def _id(a, pat):
    m = re.search(pat, a.get("href", "")) if a else None
    return m.group(1) if m else ""

def fetch(race_id, sess):
    try:
        r = sess.get(URL.format(race_id), headers=HEAD, timeout=20)
        if r.status_code != 200:
            return None, f"HTTP{r.status_code}"
        try:
            return r.content.decode("utf-8"), "ok"
        except:
            return r.content.decode("euc-jp", errors="replace"), "ok"
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

# ④ スクレイピング実行
print(f"\n[3/5] NetKeiba からスクレイピング中...")
sess = requests.Session()
all_horses = []
success_count = 0

for race_id in today_races:
    html, st = fetch(race_id, sess)
    time.sleep(random.uniform(*SLEEP))
    
    if html is None:
        print(f"  ✗ {race_id} 取得失敗: {st}")
        continue
    
    status, horses = parse_race(html, race_id)
    if status == "ok":
        all_horses.extend(horses)
        success_count += 1
        print(f"  ✓ {race_id} - {len(horses)}頭")
    else:
        print(f"  ⚠ {race_id} 解析失敗: {status}")

print(f"  成功: {success_count}/{len(today_races)} レース")
print(f"  取得馬数: {len(all_horses)}")

if len(all_horses) == 0:
    print("  ⚠ 本日のレース結果が取得できませんでした")
    exit(0)

# ⑤ 既存 pkl と統合
print(f"\n[4/5] 既存データと統合中...")
try:
    hist = pickle.load(open('history_data.pkl', 'rb'))
    print(f"  既存データ: {len(hist):,}行")
except:
    hist = pd.DataFrame()
    print(f"  既存データ: なし")

new_df = pd.DataFrame(all_horses)
hist = pd.concat([hist, new_df], ignore_index=True)
print(f"  統合後: {len(hist):,}行")

# ⑥ pkl に保存
print(f"\n[5/5] pkl を保存中...")
with open('history_data.pkl', 'wb') as f:
    pickle.dump(hist, f)
print(f"  ✓ 保存完了")

print("\n" + "=" * 80)
print("✓ スクレイピング＆更新完了！")
print("=" * 80)
