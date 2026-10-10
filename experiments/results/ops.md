# Operations

| Stage | p50 | p95 |
|---|---:|---:|
| Correlation (ms / event, 18 events) | 0.03 | 0.05 |
| Retrieval (ms / query) | 10.05 | 11.91 |

Cold start: 3.4 s (embedding model load + indexing 10 runbooks (model already in the HF cache)).
LLM: not measured here; run python -m experiments.model_selection.
