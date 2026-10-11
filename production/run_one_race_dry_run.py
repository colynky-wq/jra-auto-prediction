# ============================================================
# KeibaAI GitHub Actions - one race dry-run launcher
#
# The actual replay logic lives in dry_run_one_race.py.
# For the entry table, use the same JRA accessD URL-discovery pattern that
# succeeded in the formal 2026-10-11 Colab run.
# ============================================================

import dry_run_one_race as dryrun
from jra_entry_source import fetch_entry_table as fetch_jra_entry_table


# Replace only the entry-source function.
# Model, 37 features, odds, thresholds, and comparison logic are unchanged.
def _fetch_entry_table(race_id):
    return fetch_jra_entry_table(
        race_id=str(race_id),
        date8=dryrun.TEST_DATE8,
        jst=dryrun.JST,
    )


dryrun.fetch_entry_table = _fetch_entry_table


if __name__ == "__main__":
    dryrun.main()
