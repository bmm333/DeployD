# Retrieval

30 scored text queries (`experiments/datasets/retrieval_queries.json`) and 3 with no correct runbook, against 11 runbooks. Every query goes through `HybridRetriever.retrieve_scored`; per-signal rankings come from its breakdown. No query carries structural context. Random recall@3 = 0.27.

| Variant | Recall@1 | Recall@3 | MRR | Queries |
|---|---:|---:|---:|---:|
| text only — semantic | 0.93 | 1.00 | 0.97 | 30 |
| text only — bm25 | 0.63 | 0.73 | 0.71 | 30 |
| text only — all | 0.80 | 1.00 | 0.88 | 30 |
| hybrid, original queries | 0.92 | 1.00 | 0.94 | 12 |
| hybrid, paraphrase queries | 0.69 | 1.00 | 0.82 | 13 |
| hybrid, hard_negative queries | 0.80 | 1.00 | 0.90 | 5 |
| labels as structure (old setup, leakage) | 1.00 | 1.00 | 1.00 | 12 |

No-match queries scoring below the 0.5 threshold (correctly no strong match): 3/3.
