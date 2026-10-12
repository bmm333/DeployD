"""The EC2 scenario: ten instances, four of them upgraded by the platform, a green release.

The log is a production-like day (~900 events). What matters is what the engine concludes
from it and how early, so these tests replay it and ask the questions an on-call engineer
would ask, then take the evidence away one piece at a time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.adapters.outgoing.registry.json_topology import load_topology
from deployd.application.use_cases.correlate_events import (
    CorrelateEventsUseCase,
    compute_incident_severity,
)
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import CoreEvent, CoreEventType
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

DATA = Path("data")
SCENARIO = DATA / "scenarios" / "live_ec2_os_upgrade.json"
BAD = ["web-03", "web-05", "web-07", "web-09"]


def _events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = json.loads(SCENARIO.read_text())
    return events


def _replay(
    events: list[dict[str, Any]], retain_changes: bool = True
) -> tuple[IncidentGraph, dict[str, Any] | None]:
    """Replay *events*; also return the event at which the incident first became Critical."""
    config = CorrelationConfig(**json.loads((DATA / "correlation_config.json").read_text()))
    graph = IncidentGraph()
    window = SlidingWindow(
        change_retention_seconds=config.change_lookback_seconds if retain_changes else None
    )
    use_case = CorrelateEventsUseCase(
        graph=graph,
        event_window=window,
        config=config,
        topology=load_topology(DATA / "components.json"),
    )
    adapter = HttpEventAdapter()
    first_critical = None
    for raw in events:
        use_case.ingest(adapter.translate(RawTelemetryEvent(**raw)))
        if first_critical is None and compute_incident_severity(graph) == "Critical":
            first_critical = raw
    return graph, first_critical


def _findings(graph: IncidentGraph) -> list[CoreEvent]:
    return [
        n.event
        for n in graph.nodes
        if n.event.event_type is CoreEventType.FLEET_DIVERGENCE_DETECTED
    ]


def test_the_log_is_a_long_production_like_day() -> None:
    events = _events()

    assert len(events) > 800
    assert (
        events[0]["timestamp"]
        < "2026-10-08T03:00:00Z"
        < "2026-10-08T18:00:00Z"
        < events[-1]["timestamp"]
    )
    assert {"GIT_PUSH", "PIPELINE_JOB", "HOST_UPGRADE", "DEPLOY_START", "HEALTHCHECK_PASS"} <= {
        e["event_type"] for e in events
    }


def test_the_engine_names_the_upgrade_that_only_the_failing_instances_received() -> None:
    graph, _ = _replay(_events())

    (finding,) = _findings(graph)
    assert finding.metadata["affected"] == BAD
    assert len(finding.metadata["healthy"]) == 6  # type: ignore[arg-type]  # a list of names
    assert finding.metadata["separating_changes"] == ["os_upgrade patch-baseline-2026-10-07"]


def test_the_release_is_excluded_because_every_instance_got_it() -> None:
    (finding,) = _findings(_replay(_events())[0])

    assert finding.metadata["excluded_changes"] == ["deploy 2.14.0"]
    assert "deploy 2.14.0 on all 10, excluded" in finding.description


def test_the_finding_is_reached_minutes_after_the_first_error_not_hours() -> None:
    events = _events()
    graph, first_critical = _replay(events)

    assert first_critical is not None
    first_error = next(
        e for e in events if e["source"] in BAD and e["metadata"].get("status_code") == 500
    )
    rollback = next(
        e
        for e in events
        if e["event_type"] == "PIPELINE_JOB" and e["metadata"].get("job") == "rollback-prod"
    )
    assert first_error["timestamp"] < first_critical["timestamp"] < rollback["timestamp"]
    assert first_critical["timestamp"][:13] == "2026-10-08T10"  # within the hour of the release
    assert compute_incident_severity(graph) == "Critical"


def test_the_noise_of_the_day_does_not_become_evidence() -> None:
    graph, _ = _replay(_events())
    nodes = {n.event.related_component for n in graph.nodes}

    # a nightly backup latency spike and a flapping target stay isolated or out of the graph;
    # the slow CI job and the git pushes are context, not causes
    assert not nodes & {"gitlab", "gitlab-ci", "web-staging", "web-04", "web-02"}


def test_the_finding_does_not_name_a_package_or_a_vendor() -> None:
    (finding,) = _findings(_replay(_events())[0])
    text = json.dumps([finding.description, finding.metadata]).lower()

    assert not any(
        word in text for word in ("openssl", "libssl", "glibc", "libc6", "ubuntu", "kernel")
    )


def _drop_upgrade_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event_type"] != "HOST_UPGRADE"]


def _upgrade_everywhere(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    template = next(e for e in events if e["event_type"] == "HOST_UPGRADE")
    extra = [
        {**template, "source": f"web-{i:02d}", "timestamp": "2026-10-08T02:30:00Z"}
        for i in range(1, 11)
        if f"web-{i:02d}" not in BAD
    ]
    return sorted([*events, *extra], key=lambda e: e["timestamp"])


@pytest.mark.parametrize(
    "variant",
    [_drop_upgrade_events, _upgrade_everywhere],
    ids=["no-upgrade-recorded", "upgrade-on-every-instance"],
)
def test_without_a_change_that_separates_the_groups_there_is_no_finding(
    variant: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    graph, _ = _replay(variant(_events()))

    assert _findings(graph) == []


def test_the_hours_between_the_upgrade_and_the_errors_need_a_longer_memory_than_the_window() -> (
    None
):
    graph, _ = _replay(_events(), retain_changes=False)

    assert _findings(graph) == []
