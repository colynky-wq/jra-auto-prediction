import pandas as pd
import pickle
import numpy as np
from sklearn.linear_model import LogisticRegression
import gspread
from google.oauth2 import service_account
from datetime import datetime
import os
import json
import warnings
import time

warnings.filterwarnings('ignore')

print("=" * 80)
print("自動日次予測（GitHub Actions）")
print("=" * 80)

# 1. 環境変数から認証情報を取得
creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
if not creds_json:
    print("✗ エラー: GOOGLE_CREDENTIALS_JSON が設定されていません。")
    exit(1)

try:
    creds_info = json.loads(creds_json)
    # ★ スコープに cloud-platform を追加してエラーを強制回避します
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/cloud-platform'
    ]
    credentials = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(credentials)
    print("  ✓ Google認証成功")
except Exception as e:
    print(f"✗ 認証エラー: {e}")
    exit(1)

SHEET_ID = os.getenv('SHEET_ID')
sh = gc.open_by_key(SHEET_ID)

# 今日の日付
today = datetime.now()
today_str = today.strftime("%Y%m%d")
print(f"\n【実行日時】{today.strftime('%Y年%m月%d日')}")

# pkl を読み込み
print("\n[1/3] モデル学習中...")
try:
    hist = pickle.load(open('history_data.pkl', 'rb'))
except Exception as e:
    print(f"✗ pkl 読み込みエラー: {e}")
    exit(1)

hist['日付'] = pd.to_datetime(hist['レースID'].astype(str).str[:8], format='%Y%m%d')
hist['年'] = hist['日付'].dt.year

df = hist[hist['年'] >= 2022].reset_index(drop=True)
df['着順_num'] = pd.to_numeric(df['着順'], errors='coerce').fillna(999)
df['人気_num'] = pd.to_numeric(df['人気'], errors='coerce').fillna(999)
df['単勝オッズ_num'] = pd.to_numeric(df['単勝オッズ'], errors='coerce').fillna(0)
df['1着フラグ'] = (df['着順_num'] == 1).astype(int)
df['能力点数_num'] = pd.to_numeric(df['能力点数'], errors='coerce').fillna(1500)
df['過去5走平均着順_num'] = pd.to_numeric(df['過去5走平均着順'], errors='coerce').fillna(999)
df['過去5走1着率_num'] = pd.to_numeric(df['過去5走1着率'], errors='coerce').fillna(0)

train_df = df[df['年'] <= 2025].copy()
X_train = train_df[['能力点数_num', '過去5走平均着順_num', '過去5走1着率_num', '人気_num']].values
y_train = train_df['1着フラグ']

model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)
print("  ✓ 学習完了")

# 今日のレースを抽出
print(f"\n[2/3] {today.strftime('%m月%d日')}のレースを抽出中...")

target_races = df[df['レースID'].astype(str).str[:8] == today_str].copy()

if len(target_races) == 0:
    print(f"  ⚠ {today.strftime('%m月%d日')}にはレースがありません")
else:
    X_target = target_races[['能力点数_num', '過去5走平均着順_num', '過去5走1着率_num', '人気_num']].values
    pred_prob = model.predict_proba(X_target)[:, 1]
    
    target_races_eval = target_races.copy()
    target_races_eval['1着確率'] = pred_prob
    target_races_eval['期待値'] = target_races_eval['単勝オッズ_num'] * pred_prob
    
    mask_pop = (target_races_eval['人気_num'] >= 4) & (target_races_eval['人気_num'] <= 10)
    mask_exp = target_races_eval['期待値'] >= 1.0
    buy_df = target_races_eval[mask_pop & mask_exp].copy()
    
    print(f"  総出走馬数: {len(target_races):,}点")
    print(f"  推奨馬数: {len(buy_df):,}点")
    
    print(f"\n[3/3] Google Sheets に書き込み中...")
    
    output_df = buy_df[['レースID', '馬名', '人気_num', '単勝オッズ_num', '1着確率', '期待値']].copy()
    output_df.columns = ['レースID', '馬名', '人気', 'オッズ', '推定確率', '期待値']
    output_df = output_df.sort_values('レースID').reset_index(drop=True)
    
    try:
        ws_recommend = sh.worksheet("日次推奨")
        ws_recommend.clear()
        time.sleep(1)
        
        write_data = [['実行日時', today.strftime('%Y年%m月%d日'), '推奨馬数', len(buy_df)]]
        write_data.append(['レースID', '馬名', '人気', 'オッズ', '推定確率', '期待値'])
        
        for idx, row in output_df.iterrows():
            write_data.append([
                str(row['レースID']),
                row['馬名'],
                str(int(row['人気'])),
                f"{row['オッズ']:.2f}",
                f"{row['推定確率']:.4f}",
                f"{row['期待値']:.2f}"
            ])
        
        ws_recommend.update("A1", write_data)
        print(f"  ✓ {len(buy_df):,}点を Google Sheets に書き込み完了")
        
    except Exception as e:
        print(f"  ✗ エラー: {e}")

print("\n" + "=" * 80)
print("✓ 自動実行完了！")
print("=" * 80)
