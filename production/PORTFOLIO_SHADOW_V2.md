# KeibaAI Portfolio Shadow V2 protocol

## Status

Research shadow only. It does not enable formal recommendations, automatic betting, or stake sizing.

- Rule version: `portfolio-shadow-v2-20261101`
- Frozen v1 baseline: `portfolio-shadow-v1-20261019`
- V2 prospective start: **2026-11-02**
- Dates through 2026-11-01: **post-hoc replay only; never V2 performance evidence**

## Why V2 exists

The first frozen v1 review showed two separate questions that should not be mixed into one tuned rule:

1. High-chaos races labelled battle grade `S` behaved differently from `A` in the initial review.
2. The formal T-10 ranking could place the eventual winner inside its Top3 while the two selected Wide tickets did not cover that horse. V1 ranks Wide candidates primarily by `practicalRank`, independently of the formal horse Top3.

V1 must remain unchanged. V2 therefore records independent challengers so future races can identify which hypothesis, if either, is useful.

## Frozen challengers

### `battle_a_only`

Only for chaos levels 4-5:

- grade `A`: use the frozen v1 ticket logic unchanged;
- grade `S`: hold/skip;
- all v1 probability, EV, Trio, ticket-count and stake-unit thresholds remain unchanged.

Low/medium chaos behavior is inherited from v1 unchanged.

### `top3_coverage`

Only for chaos levels 4-5:

- keep v1 grades `A` and `S` eligible;
- keep v1 Wide probability gate `>= 5%`;
- keep v1 conservative expected-multiplier gate `>= 1.10`;
- keep maximum two Wide tickets;
- obtain the formal horse Top3 from the frozen `live_10min` Win sidecar;
- **every selected Wide ticket must contain at least one horse from that formal Top3**;
- discard any Wide pair that passes probability/EV gates but contains no formal-Top3 horse;
- among the remaining eligible pairs, choose up to two tickets that cover the largest number of distinct formal Top3 horses;
- if only one eligible Top3-containing Wide pair exists, select only that one rather than padding with an off-Top3 pair;
- after Top3 coverage, break ties using the existing practical-rank / practical-score / probability information;
- keep the v1 Trio gate unchanged.

If valid formal T-10 Top3 evidence is missing, or no Wide pair both contains a formal-Top3 horse and passes the frozen v1 probability/EV gates, fail closed and hold the Top3 challenger.

### `combined`

Apply both high-chaos changes at once: grade A only + strict Top3-membership/coverage Wide selection.

## Evidence / leakage policy

Selection may read only pre-race saved inputs:

- `SignalData.json`;
- `BetResearchData.json`;
- `reports/YYYYMMDD/all_bet_v1/win/<raceID>.json` formal T-10 sidecar.

Finish position, hit flags, payout, return and profit are forbidden from ticket selection. They may be attached only afterward for evaluation. The implementation keeps pre-race ticket objects separate from settlement fields until selection is complete.

The 2026-11-01 replay is diagnostic only because V2 was defined after those races completed. No replay result can be counted toward V2 forward performance.

## Evaluation plan

Keep all three challengers running in parallel. Do not promote a challenger from one race day. Compare at minimum:

- participation / hold rate;
- settled selected races;
- hit and profitable-race counts;
- stake, return and return index;
- high-chaos A vs S behavior;
- formal-Top3 coverage of selected Wide sets;
- how often strict Top3 membership causes a hold or reduces two Wide tickets to one.

The existing v1 remains the control and continues its original forward cohort. Threshold changes, if later justified, require another version rather than editing v1 or v2 in place.
