# Retrieval

28 scored text queries (`experiments/datasets/retrieval_queries.json`) and 3 with no correct runbook, against 10 runbooks. Every query goes through `HybridRetriever.retrieve_scored`; per-signal rankings come from its breakdown. No query carries structural context. Random recall@3 = 0.30.

| Variant | Recall@1 | Recall@3 | MRR | Queries |
|---|---:|---:|---:|---:|
| text only — semantic | 0.93 | 1.00 | 0.96 | 28 |
| text only — bm25 | 0.61 | 0.71 | 0.70 | 28 |
| text only — all | 0.82 | 0.96 | 0.90 | 28 |
| hybrid, original queries | 1.00 | 1.00 | 1.00 | 12 |
| hybrid, paraphrase queries | 0.67 | 0.92 | 0.80 | 12 |
| hybrid, hard_negative queries | 0.75 | 1.00 | 0.88 | 4 |
| labels as structure (old setup, leakage) | 1.00 | 1.00 | 1.00 | 12 |

No-match queries scoring below the 0.5 threshold (correctly no strong match): 3/3.
