# KeibaAI Persistent Handoff Guide

Last prepared: 2026-10-31 JST
Repository: `colynky-wq/jra-auto-prediction`

## Purpose

This file is the persistent handoff entry point for KeibaAI.

If ChatGPT is downgraded, a new chat is opened, memory is incomplete, or the project is resumed after a long gap, start here and reconstruct the project from the repository rather than from conversational memory.

**Repository data and immutable prediction evidence are authoritative. Chat history is not the source of truth.**

---

## 1. Non-negotiable evaluation rules

1. Never use post-race information to create or alter a pre-race prediction.
2. Formal prospective evidence is the immutable **T-10 capture**, normally 8–12 minutes before post time.
3. The first formal 100-race cohort is chronological and locked by rule beginning 2026-10-24.
4. Do not tune the current model, thresholds, race-selection logic, ticket selection, or stakes using outcomes inside the first-100 cohort.
5. Any improvement must be created as a separate **shadow / next-version candidate** and evaluated prospectively.
6. Keep these three concepts separate at all times:
   - strict formal T-10 prospective cohort,
   - all-history/reference metrics,
   - selective Portfolio strategy.
7. 100R is a diagnostic checkpoint, not proof of profitability.
8. Around 300R is the first practical candidate-selection / promotion checkpoint.
9. Around 1000R+ is the first serious long-run usefulness checkpoint for small predictive edges.
10. After 1000R, continue champion/challenger evaluation: keep the formal model stable, test successors in shadow, promote only after prospective evidence.

---

## 2. Frozen horse-strength models

Current frozen production horse models:

- `production/lightgbm_fixed_2025.txt` — Win
- `production/lightgbm_top2_fixed_2025.txt` — Top2
- `production/lightgbm_top3_fixed_2025.txt` — Top3

Do not silently retrain or replace these while evaluating the locked current-A cohort.

---

## 3. Formal T-10 evidence and first-100 review

Primary files:

- `PortfolioReview100.json`
- `production/formal_10min_evidence.py`
- `production/portfolio_review_100.py`
- `production/refresh_portfolio_review_live.py`
- `production/portfolio_review_100_diagnostics.py`
- `production/test_formal_10min_evidence.py`
- `production/test_portfolio_review_100.py`
- `production/test_portfolio_review_100_diagnostics.py`
- `production/test_refresh_portfolio_review_live.py`

Formal evidence location:

`reports/YYYYMMDD/all_bet_v1/win/<raceID>.json`

When resuming, **read `PortfolioReview100.json` first** and use its `racesObserved` as the formal count. Do not infer the formal count from raw race totals.

As of the preparation of this handoff:

- `reviewStatus`: `collecting`
- formal `racesObserved`: **91**
- `remainingTo100`: **9**
- `first100CohortLockedByRule`: `true`
- 2 prospective races were excluded because formal Win sidecar evidence was missing.

These numbers are a snapshot only. Always re-read the live JSON after resuming.

---

## 4. Automatic 100R diagnostics

Diagnostics version:

`portfolio-review-100-diagnostics-v1-20261031`

Files generated when ready:

- `PortfolioReview100Diagnostics.json`
- `PortfolioReview100Diagnostics.md`

The diagnostic generator must not publish a final diagnostic before all of the following are true:

- first-100 cohort is locked,
- `reviewWindowRaces == 100`,
- `racesObserved >= 100`,
- `remainingTo100 == 0`,
- `reviewStatus == "ready_for_short_term_review"`,
- all 100 required results are available.

If the 100th formal prediction exists but its result is not settled, status should remain result-pending and no final diagnostic should be treated as complete.

Fixed diagnostic areas include:

- overall selected/skipped performance,
- hit/profitable/torigami/stake/return/profit/return index,
- top predicted horse win rate,
- top predicted horse Top3 rate,
- actual winner in model Top3 rate,
- mean actual-winner predicted rank,
- mean number of actual Top3 horses captured by model Top3,
- battle grade,
- chaos level,
- plan type,
- selection class,
- venue,
- surface,
- distance band,
- field size,
- top-horse popularity,
- top-horse AI probability,
- top-horse odds,
- ticket type,
- skip reasons,
- focus metrics for S, A, wide_focus, wide_plus_trio, chaos5.

Interpret the 100R output as **diagnostic evidence only**. Do not declare profitability proven at 100R.

---

## 5. Automatic workflow

Workflow:

`.github/workflows/keibaai-formal-review-live.yml`

It runs the formal-evidence/review/diagnostic test suite and refreshes the review during JRA racing hours.

Important implementation detail:

- checkout is intentionally shallow: `fetch-depth: 1`
- workflow has concurrency protection
- diagnostics are only committed when materially generated/changed

A previous cancellation problem was caused by full-history checkout interacting with frequent scheduled runs; shallow checkout fixed that bottleneck. Do not revert to `fetch-depth: 0` without a specific reason.

---

## 6. Portfolio strategy: current status

Primary file:

`PortfolioResearchData.json`

Current Portfolio is research/evaluation only. At handoff preparation it reports:

- `status`: `research_display_only`
- `formalRecommendationsEnabled`: `false`
- `autoBetEnabled`: `false`
- `stakeSizingEnabled`: `false`

Current plan families include:

- `win_single`
- `win_plus_wide`
- `wide_focus`
- `wide_plus_trio`
- `skip`

At the 91R snapshot, the selective Portfolio had 17 selected races and 74 skips. The snapshot showed overall stake 3,500 yen, return 5,350 yen, profit +1,850 yen, but this is **not** proof of long-term profitability.

A notable diagnostic signal at that snapshot was:

- S selected: 9, hits 0, stake 2,000, return 0
- A selected: 8, hits 3, stake 1,500, return 5,350

Do not tune S/A thresholds before the locked 100R diagnostic completes. Treat this only as a hypothesis to investigate.

---

## 7. Ticket-specific shadow research

The ticket-specific shadow system began prospectively on 2026-10-31.

Primary runtime:

`production/ticket_probability_runtime.py`

Runtime version at introduction:

`ticket-probability-runtime-v1.3-20261031-algebraic-pl`

The shadow does **not** replace the horse winner model. It uses the frozen Formal-A Win/Top2/Top3 marginals to fit a race-local coherent ranking distribution and derives joint ticket probabilities.

Research ticket types:

- Wide
- Quinella / 馬連
- Exacta / 馬単
- Trio / 3連複
- Trifecta / 3連単

Important distinction:

- current formal Portfolio recommendations are still mostly Wide plus some Trio,
- the five ticket types above have research probability/ranking evaluation,
- they must not be described as fully formal recommendations until promotion criteria are preregistered and passed prospectively.

Direct A-vs-shadow comparison implementation:

`production/ticket_shadow_comparison.py`

Initial 2026-10-31 comparison showed very similar top coverage. Formal A was microscopically better on several calibration/NLL measures while shadow slightly improved winning-ticket rank in some Wide/Quinella/Exacta comparisons. The sample was too small for switching. Do not promote based on that single day.

---

## 8. Evaluation roadmap

### 100R — diagnostic checkpoint

Goal:

- diagnose horse-ranking quality,
- diagnose selection logic,
- diagnose battle grade / chaos / race-condition weaknesses,
- identify ticket-generation weaknesses,
- define the next challenger without changing current A retrospectively.

Immediately after the 100R diagnostic:

1. read `PortfolioReview100Diagnostics.json` and `.md`,
2. separate problems into:
   - horse-strength prediction,
   - race selection,
   - ticket selection,
   - ticket construction,
   - stake sizing,
3. define one or more v2/shadow challengers,
4. **preregister promotion criteria before collecting the deciding sample**,
5. keep current A frozen for fair comparison.

### ~300R — first practical promotion checkpoint

Evaluate each ticket type independently. Do not require all ticket types to promote at the same time.

At minimum examine:

- prediction calibration / probabilistic scoring,
- winner and Top3 rank quality,
- hit rate,
- return index / ROI,
- absolute profit,
- participation rate,
- maximum losing streak,
- drawdown,
- dependence on one large payout,
- performance across multiple race days,
- venue/surface/distance/field-size stability,
- A vs shadow/challenger comparison,
- comparison with simple baselines such as market popularity.

Possible states by ticket type:

`research -> beta candidate -> formal recommendation`

A ticket type may stay in research while another promotes.

### ~1000R — serious usefulness checkpoint

Evaluate whether the apparent edge is stable enough to matter rather than being sampling noise.

The model should ideally demonstrate all three:

1. horses assigned higher probability actually win/place more often in a calibrated way,
2. the model identifies market mispricing rather than merely reproducing popularity,
3. the edge survives conversion into tickets and transaction-like constraints over a long prospective sample.

Do not use ROI alone as the final criterion.

### After 1000R — continuous improvement

Use champion/challenger generations:

- A = current formal champion
- B = next shadow challenger
- C = experimental candidate

Never hot-edit A using recent outcomes. Promote B only after predetermined prospective evidence. Then freeze the promoted version and begin the next challenger cycle.

---

## 9. Recommended usefulness framework

Evaluate KeibaAI on three layers:

### Layer 1: prediction quality

Does it rank and calibrate horse outcomes better than simple alternatives?

Examples:

- top1 win rate,
- top1 Top3 rate,
- actual winner in model Top3,
- winner mean rank,
- Brier/log loss/calibration where appropriate,
- comparison with market-popularity baselines.

### Layer 2: betting conversion quality

Can prediction quality be turned into useful tickets?

Examples:

- ticket probability quality,
- winning-ticket rank,
- hit rate,
- ROI/return index,
- drawdown,
- losing streak,
- payout concentration,
- robustness by venue/condition.

### Layer 3: operational usefulness

Can the system be trusted in real use?

Examples:

- formal T-10 capture completeness,
- immutable evidence,
- no hindsight contamination,
- reproducible generation,
- clear skip logic,
- stable automation,
- app/chat presentation that distinguishes formal recommendations from research.

Suggested qualitative verdicts:

- `×` — no clear value over simple market/baseline logic
- `△` — predictive signal exists but does not convert into useful betting decisions
- `○` — useful in specific conditions or ticket types
- `◎` — stable prospective value across multiple conditions and long samples

---

## 10. What to do when resuming in ChatGPT

If returning after Free/Go use, after upgrading again, or in a new chat, use this sequence:

1. Connect GitHub if necessary.
2. Open this file: `KEIBAAI_HANDOFF.md`.
3. Read the live `PortfolioReview100.json`.
4. If present, read:
   - `PortfolioReview100Diagnostics.json`
   - `PortfolioReview100Diagnostics.md`
5. Read `PortfolioResearchData.json`.
6. Check the latest formal T-10 workflow run and tests.
7. Inspect recent shadow comparison/output files relevant to the current milestone.
8. Confirm the currently frozen production model/version before proposing any change.
9. State clearly which evidence is:
   - formal prospective,
   - shadow prospective,
   - backfill/reference only.
10. Continue from the next milestone; do not rerun completed historical work unless a specific integrity problem requires it.

### Copy/paste restart prompt

> KeibaAIを再開してください。GitHub `colynky-wq/jra-auto-prediction` の `KEIBAAI_HANDOFF.md` を最初に読み、次に `PortfolioReview100.json`、存在すれば `PortfolioReview100Diagnostics.json/.md`、`PortfolioResearchData.json`、最新の正式T-10 workflow 状況を確認してください。会話の記憶よりGitHub上の現在データを正として扱ってください。formal prospective / shadow prospective / backfill-reference を混同せず、完了済み作業をむやみに再実行しないでください。その時点での正式件数、診断状況、問題点、次にやるべき作業を整理し、必要な作業は可能な範囲でそのまま進めてください。

---

## 11. What not to do after resuming

- Do not replace the formal count with all-history race count.
- Do not backfill a missing formal T-10 prediction after the race and count it as formal.
- Do not optimize first-100 rules using first-100 outcomes and then pretend the same 100R validates the change.
- Do not merge formal-A and shadow metrics into one performance number.
- Do not treat one large payout as proof of edge.
- Do not call research ticket probabilities formal recommendations prematurely.
- Do not rerun/rewrite immutable pre-race evidence merely to make coverage look complete.
- Do not change production because of a single race day or one headline race.

---

## 12. ChatGPT subscription strategy

KeibaAI prediction quality should be determined by the repository models/code/data, not by the ChatGPT subscription tier.

A lower ChatGPT tier can be used during passive data collection if repository automation remains healthy. Higher-capability ChatGPT periods are most valuable for:

- 100R diagnostic interpretation and v2 design,
- ~300R promotion evaluation and code/model revisions,
- ~1000R long-run usefulness review,
- major bug investigations,
- large model-generation changes.

Before downgrading, ensure this handoff remains in the repository and automation is healthy. On return, follow the restart sequence above.

---

## 13. Living-document rule

This handoff should be updated at major milestones, especially:

- after the 100R diagnostic,
- after promotion criteria are preregistered,
- around 300R evaluation,
- whenever a challenger is promoted to formal,
- around 1000R evaluation,
- after any major change to formal evidence rules or workflow architecture.

When updating, preserve the historical non-negotiable rules unless there is an explicit, documented methodology change.
