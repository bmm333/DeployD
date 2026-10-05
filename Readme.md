# DeployD

## Deployment Intelligence Platform for Continuous Incident Detection and Diagnosis

DeployD is a deployment intelligence platform designed to reduce the gap between incident detection and root cause analysis.

Other observability systems are good at detecting anomalies and alerting engineers. The difficult part comes afterwards, where engineers still have to correlate deployments, app failures, infra events, config changes, historical incidents and system dependencies to determine what actually happened.

In DeployD we approach this problem as a continuous graph reasoning problem. The system maintains a dynamic graph representing system behaviour, events, deployments, dependencies and outcomes. New observations update the graph, while deterministic reasoning and evidence retrieval operate over its current state.

The goal is not to replace deterministic monitoring or human engineers with an LLM. DeployD combines graph-based reasoning, deterministic analysis, historical evidence retrieval and constrained AI-assisted diagnosis to progressively close the gap between detection and response.

The rule the project is built around: **never hand a probabilistic model a task a deterministic algorithm can already solve.**

## Quickstart

Requirements: Docker with Compose v2, ~3 GB of disk, a free [Groq](https://console.groq.com) API key.

```bash
cp .env.example .env        # then set GROQ_API_KEY in .env
docker compose up --build
```

| Service | URL |
|---|---|
| Live UI (Streamlit) | http://localhost:8501 |
| API (FastAPI, docs at `/docs`) | http://localhost:8000 |
| Chroma vector store | internal to the compose network |

The first investigation downloads the embedding model (`all-MiniLM-L6-v2`, ~90 MB), so it is slower than the next ones. Without `GROQ_API_KEY` everything works except Tier-3 diagnoses, which then report "no agent configured".

## Demo walkthrough

In the UI, upload one of the files below (or paste its JSON), keep **Replay: rebase timestamps to now** checked and press **Send one by one**. Press **Reset incident** between scenarios.

| Scenario | File | Expected result |
|---|---|---|
| Anomalies, no cascade | `data/scenarios/live_degrading_no_trigger.json` | Degrading — no investigation, no LLM, zero tokens |
| Novel incident | `data/scenarios/live_novel_analytics_db.json` | Tier 2 CHAIN_ONLY — causal chain shown, no historical match, LLM not called (and why) |
| OOM on auth-service | `data/scenarios/live_oom_auth_service.json` | Tier 3 FULL — grounded diagnosis, then multi-turn follow-ups |
| Elasticsearch → search | `data/scenarios/live_search_es_cascade.json` | Tier 3 FULL |
| Postgres → payment | `data/scenarios/live_payment_db_timeout.json` | Tier 3 FULL |

In a FULL investigation, ask follow-up questions in the chat. Asking about a component outside the investigation (e.g. `payment-service` during the OOM scenario) is refused: the agent only discusses evidence that went through the gate.

## How it works

```
telemetry event ─► HttpEventAdapter ─► correlator: RULE-01..06, topology-aware,
                                        5-minute event-time window
                                                │
                                                ▼
                                   IncidentGraph ─► severity: a causal chain of
                                                    2+ hops (A → B → C) = CRITICAL
                                                                │ trigger
                                                                ▼
     hybrid retrieval over historical runbooks ──►  three-tier gate
     (semantic + BM25 + causal-chain LCS +            Tier 1 INCONCLUSIVE  no evidence      → no LLM
      component Jaccard)                              Tier 2 CHAIN_ONLY    no runbook ≥ 0.5 → no LLM
                                                      Tier 3 FULL          chain + match    → Agno agent (Groq)
                                                                │
                                                                ▼
                     decision trace + grounded, validated diagnosis + multi-turn chat (live UI)
```

- **Deterministic core** — correlation rules link a cause to an effect only through the declared topology (`calls` in `data/components.json`) or a dependency named by the event; the window follows event time, so recorded scenarios replay identically.
- **AI layer** — reached only in Tier 3. The agent gets the causal chain and the retrieved runbooks, may call read-only tools (`search_runbooks`, `get_runbook_detail`, `check_component_dependencies`), and its citations are checked against the evidence set; provider errors fail closed to the deterministic result.
- Architecture decisions are recorded in [`ADR/`](ADR/).

## Development

The lockfiles target Python 3.10 on Linux x86_64 with CPU-only PyTorch.

```bash
python3.10 -m venv .venv
.venv/bin/pip install uv
.venv/bin/uv pip sync --python .venv/bin/python requirements-dev.lock \
    --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
.venv/bin/uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/pre-commit install
```

| Task | Command |
|---|---|
| Tests + coverage (gate 80%) | `.venv/bin/pytest` |
| Lint / format / types | `.venv/bin/pre-commit run --all-files` |
| API locally | `.venv/bin/uvicorn deployd.entrypoints.api:app --port 8000 --env-file .env` |
| Live UI locally | `.venv/bin/streamlit run demo/live_app.py` |
| Offline scenario demo (no API, stub agent) | `.venv/bin/streamlit run demo/app.py` |
| Seed the runbook vector store | `.venv/bin/python scripts/seed_runbooks.py` |
| Retrieval evaluation (recall@3, gate-open rate) | `.venv/bin/python scripts/eval_retrival.py` |

To change dependencies, edit `pyproject.toml` and regenerate both lockfiles with the command written at the top of `requirements.lock` / `requirements-dev.lock` (add `--extra dev` for the dev lock).

## Troubleshooting

- **"LLM provider rate limit was hit"** — the Groq free tier allows ~8k tokens/minute; a diagnosis uses ~3k and a follow-up ~2k. Wait a minute between Tier-3 scenarios.
- **No causal edges for a new service** — declare who it calls in `data/components.json` (`calls`), or send events with `metadata.dependency`.
- **Lockfile fails on macOS / ARM** — the locks are resolved for Linux x86_64; use Docker, or regenerate them for your platform with the command in the lockfile header.

## Limitations

- Single-tenant proof of concept: one incident at a time, kept in process memory; `/chat` and `/reset` assume a single active incident.
- Events are simulated; there are no real collectors yet.
- Topology and component versions are declared in `data/components.json`, not discovered.
- The runbook corpus is small (10 incidents), and Groq free-tier limits apply.

## Contributions

| Area | Arben Mema | Luca Lupi |
|---|---|---|
| Domain | CoreEvent (DID-2), IncidentGraph (DID-3), ProcessHealthFSM (DID-6) | Investigation entity (DID-4), causal engine traversal (DID-8), detection rules (DID-9) |
| Application | foundation wiring and use cases (DID-17), reactive trigger (DID-20), live investigation gate and decision trace | DTOs and contracts (DID-5), three-tier orchestrator (DID-11), structured output and multi-turn (DID-18) |
| AI / retrieval | hybrid RAG pipeline and evaluation (DID-7), runbooks and labeled queries (DID-10), Agno agent and tools (DID-15) | RRF fusion study (DID-19) |
| Infrastructure / UX | project setup and CI (DID-1), Docker (DID-14), scenario simulator (DID-16), live UI, dependency locking (DID-29) | Streamlit investigation demo (DID-13) |
| Hardening | topology-aware rules, event-time window, severity, agent tool validation (DID-22..25, DID-27) | API concurrency (DID-26) |

## Beyond the course

The current causal engine is a constrained traversal over a hand-built graph: correct and testable, but not the general solution to "does this live incident match this known failure pattern at large scale". That question is an instance of **continuous subgraph matching**: given a dynamic graph that changes through a stream of edge insertions, efficiently find and maintain all matches of a query pattern as the graph evolves (see SymBi, which builds on TurboFlux's spanning-tree filtering as a bidirectional DAG and reports large speedups on real and synthetic graphs).

That is a different, harder algorithmic problem than what is implemented here, and full CSM is out of scope for this project, as are real collectors. The architecture is built so that new adapters can be written and plugged into ingestion; for now we use simulated events.

## Let's get in touch

If you have a take on the architecture, on where the deterministic/AI boundary is drawn, on the graph model, or on anything else, open an issue or reach out directly. Same goes if you want to collaborate on extending it. Genuinely interested in outside perspectives, not just bug reports :D

Arben Mema, Luca Lupi
Computer Science students at Università del Piemonte Orientale.
