# Research recording coverage and Wide dead heats

The 2026-10-17 audit found a failed Tokyo 5R forecast (202605040605) that was
absent from RaceData, so forecast-based missing-record counts incorrectly said
zero. The latest main already handles residual odds for withdrawn entrants;
this change does not repeat that runtime modification or reconstruct the race.

All-pool, Triple, Wide-axis and reference-app summaries now include elapsed
races from the saved first-observed PaceRecordingSchedule, including a race
whose normal forecast failed. Missing records have no candidates, fingerprint
or successful-observation count. A matching saved monitor_failed event supplies
the reason. Future races are still waiting; broken schedules produce attention
instead of evidence of no racing. Original race dates survive midnight, and a
schedule plus a saved forecast is counted once. WIN5 retains its separate
official five-race/common-deadline contract.

Wide-axis evaluation now excludes ties at any of the first three positions
before counting matched races, selected bets, stake or return, matching the
reference display. Reports expose deadHeatExcluded and missingRecords separately
from held/no-candidate, awaiting results and invalid inputs. Frozen rule inputs,
thresholds and first prediction records are unchanged.
The existing v1 held count remains an aggregate of no-candidate and ties, so
already installed clients retain their recorded-count balance. New clients use
noCandidate and deadHeatExcluded separately, alongside missingRecords. The old
native test's permanent-zero assumption is replaced by the actual count of
saved new-rule files, plus isolated zero-record and exclusion contract checks.

Validation uses synthetic temporary fixtures and copies of existing main data,
with payout fetching disabled. The 2026-10-17 saved counts remain 34 per pool:
Tokyo 5R becomes one missing record per applicable type; added pools and Triple
retain 33 matched and one tie excluded, except frame quinella (30 matched,
three held and one excluded). Wide becomes 28 matched races / 60 bets,
five no-candidate, one tie excluded and one missing forecast. No old prediction
is created, no model is trained and no wager is made.
