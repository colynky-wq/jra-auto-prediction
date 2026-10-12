from __future__ import annotations

import io
import os
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

RACE_ID = os.environ.get("KEIBAAI_PAYOUT_PROBE_RACE_ID", "202605040502").strip()
URL = "https://race.netkeiba.com/race/result.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}


def flat(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [
            " ".join(dict.fromkeys(str(x).strip() for x in tup if str(x).strip() and str(x) != "nan"))
            for tup in out.columns
        ]
    else:
        out.columns = [str(x).strip() for x in out.columns]
    return out


def main() -> None:
    r = requests.get(URL, params={"race_id": RACE_ID}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.content.decode("utf-8", errors="replace")
    print("race:", RACE_ID, "http:", r.status_code, "bytes:", len(r.content))

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    print("contains ワイド:", "ワイド" in text)
    if "ワイド" in text:
        m = re.search(r".{0,250}ワイド.{0,700}", text)
        if m:
            print("TEXT_AROUND_WIDE:", m.group(0))

    tables = pd.read_html(io.StringIO(html))
    print("table_count:", len(tables))
    for i, raw in enumerate(tables):
        t = flat(raw)
        joined = " ".join(map(str, t.columns)) + " " + t.astype(str).to_string(index=False)
        if "ワイド" in joined or "払戻" in joined or "馬連" in joined or "3連" in joined:
            print("\n--- TABLE", i, "shape", t.shape, "---")
            print("columns:", list(t.columns))
            print(t.to_string(index=False, max_rows=30))

    # Inspect likely payout containers/classes, without dumping the whole page.
    for node in soup.find_all(["table", "dl", "div"]):
        s = node.get_text(" ", strip=True)
        if "ワイド" not in s:
            continue
        cls = " ".join(node.get("class", [])) if hasattr(node, "get") else ""
        print("\nNODE", node.name, "class=", cls, "text=", s[:1200])
        break


if __name__ == "__main__":
    main()
