# KeibaAI forward-validation policy

This document freezes the operating principles used for the live 2026 evaluation before a meaningful sample has accumulated.

## Data timing

- Race prediction is produced only from information available before the race.
- The target live timing is approximately 8–12 minutes before scheduled post.
- The first successfully captured Wide odds snapshot for a race is immutable.
- A missing Wide snapshot is never backfilled after scheduled post time.
- Final finish positions, final odds and payouts are evaluation labels only; they are never prediction-time features.

## Signal separation

- `荒れ度` measures race uncertainty / competitiveness.
- `勝負度` measures AI-versus-market opportunity.
- High chaos does not imply high opportunity, and vice versa.
- Obstacle races remain outside the flat-race production model.

## Wide evaluation

- AI Wide probability means the joint probability that both horses finish in the top three.
- Signal v2 evaluates every horse pair for forward validation.
- The app may show a compact top list, but validation is performed on all pairs so display truncation cannot bias the result.
- `conservativeExpectedValue` uses the lower edge of the displayed Wide odds range.
- `upperExpectedValue` is retained as a reference only.

## Frozen shadow ticket-plan rules

Rule version: `wide-ticket-shadow-v1-20261012`.

These rules were specified before a meaningful live sample existed and must not be tuned against later outcomes without creating a new version and restarting its evaluation clock.

- 安定型候補: maximum 1 pair per race, conservative EV >= 1.10.
- バランス型候補: maximum 2 pairs per race, conservative EV >= 1.10.
- 高期待値型候補: maximum 3 pairs per race, conservative EV >= 1.20.

The third profile is **not** yet the final `大穴型`; a true longshot-oriented budget plan will additionally need longshot composition criteria and its own forward validation.

## Review gate

A profile remains `data_collecting` until all of the following are satisfied:

- at least 300 selected/validated races,
- at least 150 selected bets,
- overall return index >= 1.00,
- lower bound of the 95% race-block bootstrap return-index interval >= 1.00,
- recent-half return index >= 0.95.

Even after every gate passes, the status becomes only `review_candidate`. Actual money allocation and automatic betting remain disabled and require a separate review/version.

## No automatic profit claim

A positive historical or forward return is not a guarantee of future profitability. Results are reported with losing streaks, sample size and uncertainty, including failed selections rather than only successful examples.
