# ============================================================
# KeibaAI GitHub Actions - one race dry-run launcher
#
# Purpose:
# netkeiba can prefix apprentice jockey names with symbols such as ◇, ▲, △.
# The historical dataset stores the underlying name without those markers.
# This launcher normalizes those display-only markers, then runs the existing
# read-only one-race dry run.
# ============================================================

import re
import unicodedata

import dry_run_one_race as dryrun


DISPLAY_MARKERS = "▲△☆★◇◆◎○◯◉●□■▽▼"


def normalized_name(value) -> str:
    s = unicodedata.normalize("NFKC", str(value or ""))
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(f"[{re.escape(DISPLAY_MARKERS)}]", "", s)
    return s.strip()


# Replace only the name-normalization helper used by the dry-run module.
# The prediction model, feature engineering, odds, and thresholds are unchanged.
dryrun.norm = normalized_name


if __name__ == "__main__":
    dryrun.main()
