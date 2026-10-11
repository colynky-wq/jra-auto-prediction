# ============================================================
# KeibaAI GitHub Actions - one race dry-run launcher
#
# The actual replay logic lives in dry_run_one_race.py.
# For the entry table, use the same JRA accessD URL-discovery pattern that
# succeeded in the formal 2026-10-11 Colab run.
#
# Historical replay note:
# RaceData.json intentionally contains only app-facing race metadata and does
# not include the formal model's race class.  Ordinary allowance race names
# contain strings such as "1勝" or "2勝", so dry_run_one_race.py can infer the
# class from the race name.  Named allowance races (特別/S) do not.  The formal
# 2026-10-11 run used meta["クラス"] from the early race metadata, so for the
# historical replay we restore those four known class values explicitly.
# This affects replay metadata only; model, features, odds and thresholds are
# unchanged.
# ============================================================

import dry_run_one_race as dryrun
from jra_entry_source import fetch_entry_table as fetch_jra_entry_table


# Replace only the entry-source function.
def _fetch_entry_table(race_id):
    return fetch_jra_entry_table(
        race_id=str(race_id),
        date8=dryrun.TEST_DATE8,
        jst=dryrun.JST,
    )


dryrun.fetch_entry_table = _fetch_entry_table


# Exact class metadata used by the formal 2026-10-11 run for named allowance
# races whose app-facing raceName does not contain the allowance class.
FORMAL_REPLAY_CLASS_BY_RACE_ID = {
    "202608040409": "2勝",  # 京都9R 円山特別
    "202605040409": "2勝",  # 東京9R 鷹巣山特別
    "202608040410": "3勝",  # 京都10R 三年坂S
    "202605040410": "3勝",  # 東京10R テレビ静岡賞
}

_original_derive_class = dryrun.derive_class


def _derive_class_for_replay(race_name):
    replay_class = FORMAL_REPLAY_CLASS_BY_RACE_ID.get(str(dryrun.TEST_RACE_ID))

    if replay_class is not None:
        print(
            "formal replay class:",
            replay_class,
            "(restored from 2026-10-11 formal race metadata)",
        )
        return replay_class

    return _original_derive_class(race_name)


dryrun.derive_class = _derive_class_for_replay


if __name__ == "__main__":
    dryrun.main()
