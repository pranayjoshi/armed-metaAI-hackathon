# Results

Written by `h2m bench --results` from the sessions under `data/sessions/`. Every value carries its n; a row with no recorded data says `TODO (not measured)`. Intervals are Wilson 95%. Nothing here is a target.

_Two people, one day; this does not establish a general result._

| Claim | Metric | Value | Source |
|---|---|---|---|
| Less effort | inputs per task and seconds per task, Armed vs scanning | TODO (not measured) | not measured |
| Proposals are right | accepted first time / proposals, by hour | TODO (not measured) | not measured |
| Stops fast and locally | software p50/p95 ms; physical stop from 60 fps video | software p50 2 ms · p95 2 ms · n=1; physical stop from 60 fps video: TODO (not measured) (self-test: synthetic EMG, virtual arm) | self-test only |
| Doesn't stop for nothing | false twitches and false braces in ten minutes of typing | TODO (not measured) | not measured |
| Learns | rule applied on the next carry; tea on the high route; refusal not recurring | TODO (not measured) | not measured |
| Transfers | second person: setup time to first task; their first refusal | TODO (not measured) | not measured |
| Costs | API calls and cost for the day | TODO (not measured): the runtime does not log per-call cost; copy the provider's usage page | not measured |

## Sessions read

- `20261027-062711-selftest` · participant `selftest` · 4 labelled trials · self-test: synthetic EMG, virtual arm, typed speech; not a result

## Self-test trials

4 trials on the virtual arm with synthetic EMG, run to check the tooling. They say nothing about people.

| Condition | Success (95% CI) | Wrong | No action |
|---|---|---|---|
| speech | 0/1 = 0% [0%, 79%] | 0 | 1 |
| speech+point+emg | 2/3 = 67% [21%, 94%] | 0 | 1 |

## Raw takes and failure clips

Raw takes, the event log and the failure clips: TODO (URL).
