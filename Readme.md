#DeployD an deployment intelligence platform

Current tools detect anomalies and alert humans, who perform root cause analysis manually (or partially).
DeployD traces and continuosly learns from prod behaviour, trying to close the loop that current tools leave open.

DeployD is a continuously evolving graph of system behaviour, events and outcomes. Every other component rd/wr from/to the graph.

We have organized the architecture into six tiers, begining with raw data ingestion to evaluation. Each tire having a single clear responsability, keeping the system modular and testable.

Tire 1: **Data Sources** - Capturing raw signals such as ci/cd events, vm metrics , app logs, pkg diffs. 
Tire 2: **Event Bus** - Normalizing and transporting events reliably and in order.
Tire 3: **Multi-Agent Core** - Detect anomalies, track deployments, correlate events, orchestrate resonse.
Tire 4: **Knowledge Layer** - Store and Retrive incident history and deployment for RAG.
Tire 5: **Response Layer** - Alert, visualizze and opt act on detected incidents.
Tire 6: **Evaluation** - Measure Precision , recall, and detection latency against simulated Incidents.

.
.
.
.
.
.
.
.
.
.
.
.
.
.
.
.
.
## Current scope
This is a project built for Programmazione di Applicazioni Intelligenti (UPO a.a. 2025/26) 
