# Operations

| Stage | p50 | p95 |
|---|---:|---:|
| Correlation (ms / event, 1713 events) | 0.02 | 0.23 |
| Retrieval (ms / query) | 10.80 | 13.32 |
| LLM diagnosis, openai/gpt-oss-120b (s) | 3.24 | 4.56 |
| LLM follow-up (s) | 2.60 | 3.18 |

Cold start: 3.3 s (embedding model load + indexing 11 runbooks (model already in the HF cache)).
Estimated LLM cost per incident: $0.0025 (from experiments/results/model_selection.json (DID-34)).
