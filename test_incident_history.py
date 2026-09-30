import time

import requests

base = "http://localhost:8081/api/v1"
print("Triggering cascade...")
requests.post(
    f"{base}/events",
    json={
        "source": "postgres-primary",
        "event_type": "LATENCY",
        "metadata": {"latency_ms": 842},
        "description": "PostgreSQL query latency spike",
        "timestamp": "2026-09-29T10:00:00Z",
    },
)
time.sleep(0.5)
requests.post(
    f"{base}/events",
    json={
        "source": "auth-service",
        "event_type": "REQUEST_TIMEOUT",
        "metadata": {"duration_ms": 5000},
        "description": "auth-service timeout",
        "timestamp": "2026-09-29T10:00:01Z",
    },
)
time.sleep(0.5)
requests.post(
    f"{base}/events",
    json={
        "source": "api-gateway",
        "event_type": "HTTP_RESPONSE",
        "metadata": {"status_code": 503},
        "description": "api-gateway 503",
        "timestamp": "2026-09-29T10:00:02Z",
    },
)

time.sleep(2)  # wait for diagnosis and graph building
state = requests.get(f"{base}/state").json()
print("Live nodes:", len(state["graphs"]["nodes"]))
print("Live edges:", len(state["graphs"]["edges"]))

print("Closing incident...")
reset_res = requests.post(f"{base}/reset").json()
closed_id = reset_res["closed_incident_id"]

if closed_id:
    inc = requests.get(f"{base}/incidents/{closed_id}").json()
    print("History nodes:", len(inc["graph_snapshot"]["nodes"]))
    print("History edges:", len(inc["graph_snapshot"]["edges"]))
else:
    print("No incident was closed!")
