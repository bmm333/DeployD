# Gate

- Trigger as in the API on develop: an investigation starts when the incident is CRITICAL (2+ hop causal chain).
- 'without_topology' replays the same events with a topology where every component calls every other, i.e. the correlation rules before the DID-22 fix.
- The agent is a stub citing the best candidate: FULL means the gate let the LLM in.

| Scenario | Expected | Decision | Best score | Expected runbook rank | Without topology | Before cosine fix |
|---|---|---|---:|---:|---|---|
| live_oom_auth_service | FULL | FULL | 0.67 | 1 | FULL (Critical) | best 0.62, gate open |
| live_payment_db_timeout | FULL | FULL | 0.67 | 1 | FULL (Critical) | best 0.60, gate open |
| live_search_es_cascade | FULL | FULL | 0.74 | 1 | FULL (Critical) | best 0.63, gate open |
| live_novel_analytics_db | CHAIN_ONLY | CHAIN_ONLY | 0.34 | – | CHAIN_ONLY (Critical) | best 0.30, gate closed |
| live_degrading_no_trigger | NO_TRIGGER | NO_TRIGGER | – | – | NO_TRIGGER (Degrading) | – |
| synthetic_false_causality | NO_TRIGGER | NO_TRIGGER | – | – | FULL (Critical) | – |

## Threshold sweep (LLM allowed = FULL)

| Threshold | Precision | Recall | F1 | Exact decisions |
|---:|---:|---:|---:|---:|
| 0.30 | 0.75 | 1.00 | 0.86 | 5/6 |
| 0.35 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.40 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.45 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.50 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.55 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.60 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.65 | 1.00 | 1.00 | 1.00 | 6/6 |
| 0.70 | 1.00 | 0.33 | 0.50 | 4/6 |
| 0.75 | 0.00 | 0.00 | 0.00 | 3/6 |
| 0.80 | 0.00 | 0.00 | 0.00 | 3/6 |
