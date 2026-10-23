from __future__ import annotations

import pandas as pd

from jra_entry_source import _parse_entry_table
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


def main() -> None:
    test_current_trainer_element()
    test_unseen_and_blank_name_fallbacks()
    print("JRA_ENTRY_NAME_HANDLING_REGRESSION_OK")


if __name__ == "__main__":
    main()
