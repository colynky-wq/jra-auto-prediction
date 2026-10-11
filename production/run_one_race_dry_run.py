# ============================================================
# KeibaAI GitHub Actions - one race dry-run launcher
#
# The actual replay logic lives in dry_run_one_race.py.
# This launcher intentionally changes nothing; it only starts main().
# ============================================================

import dry_run_one_race as dryrun


if __name__ == "__main__":
    dryrun.main()
