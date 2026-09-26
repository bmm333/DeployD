import datetime
import random
import time

import requests

API_URL = "http://localhost:8081/api/v1/events"
TOTAL_EVENTS = 300

SERVICES = [
    "web-frontend",
    "payment-service",
    "inventory-service",
    "email-worker",
    "api-gateway",
    "auth-service",
    "postgres-db",
    "redis-cache",
]

NOISE_TEMPLATES = [
    {"event_type": "METRIC", "desc": "CPU utilization at {val}%", "sev": "INFO"},
    {"event_type": "METRIC", "desc": "Memory usage at {val}MB", "sev": "INFO"},
    {"event_type": "LOG", "desc": "Garbage collection completed in {val}ms", "sev": "INFO"},
    {"event_type": "LOG", "desc": "Healthcheck OK", "sev": "INFO"},
    {"event_type": "METRIC", "desc": "Endpoint /health latency {val}ms", "sev": "INFO"},
    {
        "event_type": "LOG",
        "desc": "User login successful",
        "sev": "INFO",
        "service": "auth-service",
    },
    {"event_type": "LOG", "desc": "Payment processed", "sev": "INFO", "service": "payment-service"},
    {
        "event_type": "WARNING",
        "desc": "Slight delay in queue processing",
        "sev": "WARNING",
        "service": "email-worker",
    },
]


def send_event(service, event_type, description, severity, metadata=None):
    if metadata is None:
        metadata = {}

    payload = {
        "event_type": event_type,
        "description": description,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "source": service,
        "metadata": metadata,
    }

    try:
        requests.post(API_URL, json=payload, timeout=2)
        print(f"[{severity}] {service}: {description}")
    except Exception as e:
        print(f"Failed to send event: {e}")


print("🚀 Starting DeployD SaaS Simulation Engine...")
print(f"📦 Generating {TOTAL_EVENTS} events...")
print("-" * 50)

for i in range(1, TOTAL_EVENTS + 1):
    # Determine if we should inject the cascading failure
    if i == 150:
        print("\n💥 INJECTING ROOT CAUSE: DB Latency Spike...")
        send_event(
            service="postgres-db",
            event_type="RESOURCE_EXHAUSTION",
            description="Database query latency exceeded 5000ms. Active connections maxed out.",
            severity="CRITICAL",
            metadata={"component": "postgres-db", "metric": "query_latency", "value": "5200ms"},
        )
    elif i == 160:
        print("\n⚠️ INJECTING CASCADING EFFECT 1: Auth Service Timeout...")
        send_event(
            service="auth-service",
            event_type="CONNECTION_FAILURE",
            description="Timeout connecting to postgres-db. Connection pool exhausted.",
            severity="CRITICAL",
            metadata={"component": "auth-service", "dependency": "postgres-db"},
        )
    elif i == 170:
        print("\n🚨 INJECTING CASCADING EFFECT 2: API Gateway 500s...")
        send_event(
            service="api-gateway",
            event_type="API_ERROR",
            description="Spike in HTTP 500 responses on /api/login endpoint.",
            severity="CRITICAL",
            metadata={"component": "api-gateway", "endpoint": "/api/login", "error_rate": "85%"},
        )
    else:
        # Generate normal background noise
        template = random.choice(NOISE_TEMPLATES)
        service = template.get("service", random.choice(SERVICES))
        val = random.randint(10, 90)
        desc = template["desc"].format(val=val)

        send_event(
            service=service,
            event_type=template["event_type"],
            description=desc,
            severity=template["sev"],
            metadata={"noise": True},
        )

    # Sleep to simulate real-time log ingestion (a bit faster for demo purposes)
    time.sleep(random.uniform(0.05, 0.2))

print("-" * 50)
print("✅ Simulation complete! Check the DeployD UI to see how the SRE Agent handled the cascade.")
