# Operations

| Stage | p50 | p95 |
|---|---:|---:|
| Correlation (ms / event, 18 events) | 0.03 | 0.05 |
| Retrieval (ms / query) | 11.85 | 13.69 |
| LLM diagnosis, openai/gpt-oss-120b (s) | 3.24 | 4.56 |
| LLM follow-up (s) | 2.60 | 3.18 |

Cold start: 3.7 s (embedding model load + indexing 10 runbooks (model already in the HF cache)).
Estimated LLM cost per incident: $0.0025 (from experiments/results/model_selection.json (DID-34)).
