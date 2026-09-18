# DeployD

## Deployment Intelligence Platform for Continuous Incident Detection and Diagnosis

DeployD is a deployment intelligence platform designed to reduce the gap between incident detection and root cause analysis.

Other observability systems are good at detecting anomalies and alerting engineers. The difficult part comes afterwards, where engineers still have to correlate deployments, app failures, infra events, config changes historical incidents and system dependencies to determine what actually happened.

In DeployD we approach this problem as a continuous graph reasoning problem.

The system maintains a dynamic graph representing system behaviour, events, deployments, dependencies, and outcomes. New observations update the graph, while deterministic reasoning and evidence retrieval operate over its current state.

The goal is not to replace determinisitc monitoring or human engineers with an LLM. Instead we with DeployD combine graph based reasoning, deterministic analysis, historical evidence retrieval and constrained AI-assisted diagnosis to progressively close detection and response.

The Rule the project is built around: never hand a probabilistic model a task an deterministic algorithm can already solve.

## How we want to shape this project in the future beyond our AI course:

The current causal engine is a constrained BFS traversal over a hand built graph , corret and testable, but not the general solution to: does this live incident match this known failure pattern at a large scale. From what I've been observing and reading from papers it is really an instance of `Continuous subgraph matching` given a dynamic graph that changes through a stream of edge insertions, efficiently find and maintain all matches of a query patter as the graph evolves ( ref papers SymBi who works over TurboFlux spanning tree filtering as a bidirectional DAG and has reported large speedups on real and synthetic graphs, real huge improvments).

That is a different, harder algorithmic problem than what is currently implemented here. Full CSM is not part of this project scope ( such as real adapters , but if you want to test it out with your system i tried to build the architecture so you can just write adapters as you please and be good to ingest , we just use simulated events).

# LETS GET IN TOUCH

If you read through this and have a take on the architecture, on where the deterministic/AI boundary is drawn, on the graph model, on anything open an issue or reach out directly. Same goes if you want to collaborate on extending it. Genuinely interested in outside prespective on this, not just looking for bug reports :D.

Arben Mema, Luca Lupi
Computer Science students of Università del Piemonte Orientale.
