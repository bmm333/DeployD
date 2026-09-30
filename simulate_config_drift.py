import time
from datetime import datetime, timedelta

import requests

base = "http://localhost:8081/api/v1"

print("Resetting previous state...")
requests.post(f"{base}/reset")

now = datetime.utcnow()

# T=0: Config drift in redis
t0 = now - timedelta(minutes=5)
requests.post(
    f"{base}/events",
    json={
        "source": "redis-cache-cluster",
        "event_type": "STATE_CHANGE",
        "metadata": {
            "config_key": "maxmemory-policy",
            "old_value": "allkeys-lru",
            "new_value": "noeviction",
        },
        "description": "Config changed: maxmemory-policy to noeviction",
        "timestamp": t0.isoformat() + "Z",
    },
)
print("Config Drift generated.")
time.sleep(1)

# T=1m: Resource Exhaustion
t1 = t0 + timedelta(minutes=1)
requests.post(
    f"{base}/events",
    json={
        "source": "redis-cache-cluster",
        "event_type": "RESOURCE_EXHAUSTION",
        "metadata": {"memory_percent": 99.5},
        "description": "Redis memory at 99.5%",
        "timestamp": t1.isoformat() + "Z",
    },
)
print("Resource Exhaustion generated.")
time.sleep(1)

# T=2m: Dependency failure in user-service
t2 = t0 + timedelta(minutes=2)
requests.post(
    f"{base}/events",
    json={
        "source": "user-service",
        "event_type": "DEPENDENCY_FAILURE",
        "metadata": {"_raw_event_type": "REQUEST_TIMEOUT", "dependency": "redis-cache-cluster"},
        "description": "Timeout connecting to Redis",
        "timestamp": t2.isoformat() + "Z",
    },
)
print("Dependency Failure (user-service) generated.")
time.sleep(1)

# T=3m: HTTP 500 in user-service
t3 = t0 + timedelta(minutes=3)
for _ in range(3):
    requests.post(
        f"{base}/events",
        json={
            "source": "user-service",
            "event_type": "STATE_CHANGE",
            "metadata": {"status_code": 500},
            "description": "Internal Server Error in user-service",
            "timestamp": t3.isoformat() + "Z",
        },
    )
print("HTTP 500 Cluster (user-service) generated.")
time.sleep(1)

# T=4m: Dependency failure in api-gateway
t4 = t0 + timedelta(minutes=4)
requests.post(
    f"{base}/events",
    json={
        "source": "api-gateway",
        "event_type": "DEPENDENCY_FAILURE",
        "metadata": {"_raw_event_type": "HTTP_ERROR", "dependency": "user-service"},
        "description": "user-service returning 500",
        "timestamp": t4.isoformat() + "Z",
    },
)
print("Dependency Failure (api-gateway) generated.")
time.sleep(1)

# T=5m: HTTP 500 in api-gateway (critical)
t5 = t0 + timedelta(minutes=5)
for _ in range(3):
    requests.post(
        f"{base}/events",
        json={
            "source": "api-gateway",
            "event_type": "STATE_CHANGE",
            "metadata": {"status_code": 503},
            "description": "Gateway returning 503",
            "timestamp": t5.isoformat() + "Z",
        },
    )
print("HTTP 500 Cluster (api-gateway) generated.")

print("Scenario injected successfully!")
