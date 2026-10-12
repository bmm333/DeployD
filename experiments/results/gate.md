# Gate

- Trigger as in the API on develop: an investigation starts when the incident is CRITICAL (2+ hop causal chain).
- 'without_topology' replays the same events with a topology where every component calls every other, i.e. the correlation rules before the DID-22 fix.
- The agent is a stub citing the best candidate: FULL means the gate let the LLM in.

| Scenario | Expected | Decision | Best score | Expected runbook rank | Without topology | Before cosine fix |
|---|---|---|---:|---:|---|---|
| live_oom_auth_service | FULL | FULL | 0.67 | 1 | FULL (Critical) | best 0.62, gate open |
| live_payment_db_timeout | FULL | FULL | 0.67 | 1 | FULL (Critical) | best 0.60, gate open |
| live_search_es_cascade | FULL | FULL | 0.74 | 1 | FULL (Critical) | best 0.63, gate open |
| live_novel_analytics_db | CHAIN_ONLY | CHAIN_ONLY | 0.31 | – | CHAIN_ONLY (Critical) | best 0.30, gate closed |
| live_degrading_no_trigger | NO_TRIGGER | NO_TRIGGER | – | – | NO_TRIGGER (Degrading) | – |
| live_ec2_os_upgrade | FULL | FULL | 0.68 | 1 | FULL (Critical) | best 0.52, gate open |
| live_disk_full_single_instance | FULL | CHAIN_ONLY | 0.23 | 3 | CHAIN_ONLY (Degrading) | best 0.15, gate closed |
| live_dns_failure | CHAIN_ONLY | NO_TRIGGER | – | – | NO_TRIGGER (Degrading) | – |
| synthetic_false_causality | NO_TRIGGER | NO_TRIGGER | – | – | FULL (Critical) | – |

## Threshold sweep (LLM allowed = FULL)

| Threshold | Precision | Recall | F1 | Exact decisions |
|---:|---:|---:|---:|---:|
| 0.30 | 0.80 | 0.80 | 0.80 | 6/9 |
| 0.35 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.40 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.45 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.50 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.55 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.60 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.65 | 1.00 | 0.80 | 0.89 | 7/9 |
| 0.70 | 1.00 | 0.20 | 0.33 | 4/9 |
| 0.75 | 0.00 | 0.00 | 0.00 | 3/9 |
| 0.80 | 0.00 | 0.00 | 0.00 | 3/9 |
