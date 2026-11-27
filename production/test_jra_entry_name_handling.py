from __future__ import annotations

import pandas as pd

from jra_entry_source import _parse_entry_table, decode_jra_entry_html, JRAEntryUnavailable, JRAEntryAccessRestricted
from early_prediction_runtime import _predict_with_one_retry
from win_odds_freshness import WinOddsFreshnessError
import live_monitor_topk as live


def _row(number: int, horse: str, trainer: str, division: str, jockey: str) -> str:
    return f"""
    <tr>
      <td>1</td>
      <td>{number}</td>
      <td>
        <a href="#horse">{horse}</a>
        <p class="trainer">{trainer}<span class="division">({division})</span></p>
      </td>
      <td>牡4 58.0kg <a href="#jockey">{jockey}</a></td>
    </tr>
    """


def test_current_trainer_element() -> None:
    html = "<table>" + _row(7, "ウィルオレオール", "小国 博行", "本会外", "石川 倭") + _row(8, "テスト中央馬", "本田 優", "栗東", "幸 英明") + "</table>"
    d = _parse_entry_table(html, "202608040810")
    got = {int(r["馬番"]): str(r["調教師_JRA"]).replace(" ", "") for _, r in d.iterrows()}
    assert got[7] == "小国博行", got
    assert got[8] == "本田優", got


def test_unseen_and_blank_name_fallbacks() -> None:
    unseen = live._match_history_name_or_current(
        "山本 政聡",
        ["川田将雅", "坂井瑠星"],
        role="jockey",
        horse_no=8,
    )
    assert unseen == "山本 政聡", unseen

    missing_trainer = live._match_history_name_or_current(
        pd.NA,
        ["本田優"],
        role="trainer",
        horse_no=9,
    )
    assert missing_trainer == live.UNKNOWN_TRAINER, missing_trainer


def test_entry_encoding_and_retry() -> None:
    body = "<table>" + _row(1, "テスト馬", "小国 博行", "本会外", "石川 倭") + "</table>"
    for codec, meta in (("cp932", '<meta charset="Shift_JIS">'),
                        ("utf-8", '<meta charset="UTF-8">'),
                        ("cp932", ""), ("utf-8", "")):
        decoded = decode_jra_entry_html((meta + body).encode(codec))
        frame = _parse_entry_table(decoded, "202608040810")
        assert frame.iloc[0]["馬名"] == "テスト馬"
    try:
        decode_jra_entry_html(b'<meta charset="UTF-8">\xff')
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("invalid declared encoding accepted")

    counts = {}
    def predict(race):
        rid = race["race_id"]
        counts[rid] = counts.get(rid, 0) + 1
        if rid == "stale" and counts[rid] == 1:
            raise WinOddsFreshnessError("win_odds_source_stale_over_30m")
        if rid == "entry":
            raise JRAEntryUnavailable("tableなし")
        if rid == "blocked":
            raise JRAEntryAccessRestricted("HTTP=429")
        if rid == "model":
            raise RuntimeError("model failed")
        return rid
    rows = [{"race_id": rid} for rid in ("ok", "stale", "entry", "blocked", "model")]
    good, bad = _predict_with_one_retry(rows, predict)
    assert set(good) == {"ok", "stale"}
    assert counts == {"ok": 1, "stale": 2, "entry": 2, "blocked": 1, "model": 1}
    assert bad["entry"]["attempts"] == 2 and bad["blocked"]["attempts"] == 1
    counts.clear()
    _, bad = _predict_with_one_retry(rows, predict, retry_allowed=lambda: False)
    assert all(n == 1 for n in counts.values())
    assert bad["stale"]["attempts"] == 1


def main() -> None:
    test_current_trainer_element()
    test_entry_encoding_and_retry()
    test_unseen_and_blank_name_fallbacks()
    print("JRA_ENTRY_NAME_HANDLING_REGRESSION_OK")


if __name__ == "__main__":
    main()
