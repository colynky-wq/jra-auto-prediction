#!/usr/bin/env bash
# Refresh ONLY the strict aligned audit from the published main branch.
# Invoke after each result-sync cycle, even if optional downstream research
# failed. Never reads local uncommitted results or modifies frozen T-10 inputs.
set -euo pipefail

git config user.name 'KeibaAI GitHub Actions'
git config user.email 'keibaai@users.noreply.github.com'

for attempt in 1 2 3 4; do
  git fetch --no-tags origin main
  git reset --hard origin/main
  # Result Sync may have left untracked/ignored local reports after a failed
  # downstream build. Ignore ALL of them: the audit must read published main.
  git clean -fdx -- reports >/dev/null

  audit_failed=0
  python production/aligned_portfolio_review.py || audit_failed=1
  if [ "$audit_failed" -ne 0 ]; then
    # A failed audit may publish only fail-closed provenance, NEVER an old
    # successful cohort or beta event disguised as current evidence.
    python - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("AlignedPortfolioReview.json").read_text(encoding="utf-8"))
assert d.get("projectionState") == "failed"
assert d.get("aligned", {}).get("countsAreCurrent") is False
assert d.get("betaDecision", {}).get("eligibleForResearchBeta") is False
assert not d.get("notificationEvents")
PY
    echo '::warning::ALIGNED_AUDIT_FAILED: publishing blocked state, not current performance.'
  fi

  git add -f -- AlignedPortfolioReview.json AlignedPortfolioReview.md
  if git diff --cached --quiet; then
    echo 'ALIGNED_AUDIT_UNCHANGED: main already reflects these exact inputs.'
    exit 0
  fi

  git commit -m 'Publish independent strictly verified or blocked aligned audit'
  if git push origin HEAD:main; then
    echo 'ALIGNED_AUDIT_PUBLISHED: state derived from latest published main.'
    exit 0
  fi
  echo "::warning::Concurrent main change; revalidate audit inputs (attempt $attempt/4)."
  sleep "$((attempt * 2))"
done

echo '::error::ALIGNED_AUDIT_PUBLICATION_FAILED; current counts must not be inferred.' >&2
exit 1
