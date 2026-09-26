"""
HTTP Incoming Event Adapter.

Translates raw telemetry payloads (as emitted by external services —
EC2 instances, K8s pods, load balancers, etc.) into domain ``CoreEvent``
objects. This is the ONLY place where raw infrastructure vocabulary is
translated into domain semantics. Nothing downstream ever sees the raw payload.

Production note: this adapter pattern means that in the future you can swap
the transport (CloudWatch → Prometheus → Datadog webhook) without touching
anything in the domain layer. You only add a new adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Raw payload schema (what the outside world sends us)
# ---------------------------------------------------------------------------


class RawTelemetryEvent(BaseModel):
    """
    The schema for an incoming raw telemetry event.

    Deliberately minimal and permissive — external services only know what
    happened to *them*. They do NOT know severity, causal relationships, or
    whether an incident exists. Those are all inferred by DeployD.

    Fields
    ------
    timestamp   : ISO-8601 UTC string. Required — we never infer time.
    source      : Service or host that emitted the event (e.g. "payment-service",
                  "postgres-primary", "api-gateway-eu-west-1").
    event_type  : Raw event type label as used by the emitting service.
                  Accepted raw types are mapped to ``CoreEventType`` by the adapter.
    metadata    : Open-ended bag of key/value pairs. Numeric observations such as
                  ``latency_ms``, ``status_code``, ``cpu_percent`` live here.
                  The correlator rules operate on these values.
    description : Optional human-readable message from the emitting service.
    """

    timestamp: str = Field(..., description="ISO-8601 UTC timestamp from the emitting service")
    source: str = Field(..., description="Service/host identifier, e.g. 'payment-service'")
    event_type: str = Field(
        ...,
        description=(
            "Raw event type as known to the emitter. "
            "Example values: 'HTTP_RESPONSE', 'LATENCY', 'REQUEST_TIMEOUT', 'GC', 'CPU_SAMPLE'"
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Open-ended numeric/string observations: latency_ms, status_code, value, etc.",
    )
    description: str = Field(
        default="", description="Optional free-text description from the emitter"
    )


# ---------------------------------------------------------------------------
# Raw event type → CoreEventType mapping
# ---------------------------------------------------------------------------

# This is the canonical translation table. If a new raw event type arrives from
# an adapter, add it here. The default fallback is STATE_CHANGE.
_RAW_TYPE_MAP: dict[str, CoreEventType] = {
    # Network / HTTP
    "HTTP_RESPONSE": CoreEventType.STATE_CHANGE,
    "HTTP_ERROR": CoreEventType.DEPENDENCY_FAILURE,
    "REQUEST_TIMEOUT": CoreEventType.DEPENDENCY_FAILURE,
    "CONNECTION_ERROR": CoreEventType.CONNECTIVITY_LOSS,
    "CONNECTION_REFUSED": CoreEventType.CONNECTIVITY_LOSS,
    # Latency / resource metrics
    "LATENCY": CoreEventType.STATE_CHANGE,
    "CPU_SAMPLE": CoreEventType.RESOURCE_EXHAUSTION,
    "MEMORY_SAMPLE": CoreEventType.RESOURCE_EXHAUSTION,
    "DISK_IO": CoreEventType.RESOURCE_EXHAUSTION,
    # Process lifecycle
    "PROCESS_START": CoreEventType.DEPLOY_STARTED,
    "PROCESS_STOP": CoreEventType.PROCESS_CRASH,
    "OOM_KILL": CoreEventType.PROCESS_CRASH,
    "SEGFAULT": CoreEventType.PROCESS_CRASH,
    # Healthchecks
    "HEALTHCHECK_PASS": CoreEventType.HEALTH_CHECK_PASS,
    "HEALTHCHECK_FAIL": CoreEventType.HEALTH_CHECK_FAIL,
    # Deployment
    "DEPLOY_START": CoreEventType.DEPLOY_STARTED,
    "DEPLOY_SUCCESS": CoreEventType.DEPLOY_COMPLETED,
    "DEPLOY_FAIL": CoreEventType.DEPLOY_FAILED,
    # Infrastructure config
    "CONFIG_CHANGE": CoreEventType.CONFIG_CHANGE,
    # Queue / async
    "QUEUE_OVERFLOW": CoreEventType.RESOURCE_EXHAUSTION,
    "WORKER_TIMEOUT": CoreEventType.DEPENDENCY_FAILURE,
    # GC / runtime
    "GC": CoreEventType.STATE_CHANGE,
    "GC_PAUSE": CoreEventType.STATE_CHANGE,
}


def _map_event_type(raw_type: str) -> CoreEventType:
    """Map a raw event type string to a domain CoreEventType. Defaults to STATE_CHANGE."""
    return _RAW_TYPE_MAP.get(raw_type.upper(), CoreEventType.STATE_CHANGE)


# ---------------------------------------------------------------------------
# Severity: NOT provided by the emitter — assigned by the adapter heuristics
# then REFINED by the EventCorrelator.
#
# The adapter assigns a PROVISIONAL severity based on the raw metadata so that
# the correlator has an initial signal to work with. The correlator may UPGRADE
# severity (never downgrade) as patterns accumulate.
# ---------------------------------------------------------------------------


def _provisional_severity(event: RawTelemetryEvent) -> Severity:
    """
    Assign a provisional (locally-scoped) severity based on metric thresholds.

    This is NOT the incident severity — it is only a per-event signal. The
    actual incident severity emerges from the causal chain topology.

    Thresholds are intentionally conservative: we prefer false negatives here
    because the correlator is responsible for the final assessment.
    """
    meta = event.metadata
    raw_type = event.event_type.upper()

    # HTTP response codes
    status = meta.get("status_code")
    if isinstance(status, int):
        if status >= 500:
            return Severity.ERROR
        if status >= 400:
            return Severity.WARNING

    # Latency thresholds (ms)
    latency = meta.get("latency_ms") or meta.get("duration_ms") or meta.get("value")
    if isinstance(latency, int | float) and "LATENCY" in raw_type:
        if latency >= 2000:
            return Severity.ERROR
        if latency >= 500:
            return Severity.WARNING

    # CPU / Memory thresholds (percent)
    pct = meta.get("percent") or meta.get("cpu_percent") or meta.get("memory_percent")
    if isinstance(pct, int | float):
        if pct >= 95:
            return Severity.ERROR
        if pct >= 80:
            return Severity.WARNING

    # Explicit failure types
    if raw_type in (
        "OOM_KILL",
        "SEGFAULT",
        "DEPLOY_FAIL",
        "PROCESS_STOP",
        "CONNECTION_REFUSED",
        "HEALTHCHECK_FAIL",
        "QUEUE_OVERFLOW",
    ):
        return Severity.ERROR

    if raw_type in (
        "REQUEST_TIMEOUT",
        "CONNECTION_ERROR",
        "WORKER_TIMEOUT",
        "HEALTHCHECK_FAIL",
        "HTTP_ERROR",
    ):
        return Severity.WARNING

    return Severity.INFO


# ---------------------------------------------------------------------------
# Public adapter function
# ---------------------------------------------------------------------------


class HttpEventAdapter:
    """
    Translates a ``RawTelemetryEvent`` into a ``CoreEvent``.

    Usage
    -----
    ::

        adapter = HttpEventAdapter()
        core_event = adapter.translate(raw)

    The adapter is stateless and thread-safe. One instance can serve many
    concurrent HTTP requests.
    """

    def translate(self, raw: RawTelemetryEvent) -> CoreEvent:
        """
        Translate a raw telemetry payload into a domain ``CoreEvent``.

        Raises
        ------
        ValueError
            If the timestamp is not a valid ISO-8601 string.
        """
        try:
            ts = datetime.fromisoformat(raw.timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid timestamp format from source '{raw.source}': {raw.timestamp!r}"
            ) from exc

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        description = raw.description or self._synthesise_description(raw)

        return CoreEvent(
            event_type=_map_event_type(raw.event_type),
            timestamp=ts,
            severity=_provisional_severity(raw),
            related_component=raw.source,
            description=description,
            metadata=raw.metadata | {"_raw_event_type": raw.event_type, "_source": raw.source},
        )

    @staticmethod
    def _synthesise_description(raw: RawTelemetryEvent) -> str:
        """Generate a human-readable description when the emitter did not provide one."""
        meta_str = ", ".join(f"{k}={v}" for k, v in raw.metadata.items() if not k.startswith("_"))
        if meta_str:
            return f"{raw.event_type} from {raw.source}: {meta_str}"
        return f"{raw.event_type} from {raw.source}"
