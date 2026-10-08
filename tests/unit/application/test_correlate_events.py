"""CorrelateEventsUseCase: how rule matches become graph nodes and edges."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from deployd.adapters.incoming.http_event_adapter import HttpEventAdapter, RawTelemetryEvent
from deployd.application.use_cases.correlate_events import (
    CorrelateEventsUseCase,
    compute_incident_severity,
)
from deployd.domain.causal.causal_rule import CorrelationRuleFn, RuleMatch
from deployd.domain.causal.config import CorrelationConfig
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.graph.graph import IncidentGraph
from deployd.infrastructure.streaming.sliding_window import SlidingWindow

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)


def test_unrelated_services_produce_no_causal_edges() -> None:
    graph = IncidentGraph()
    use_case = CorrelateEventsUseCase(
        graph=graph, event_window=SlidingWindow(), config=CorrelationConfig()
    )
    adapter = HttpEventAdapter()
    unrelated = [
        ("billing-batch", "CPU_SAMPLE", {"cpu_percent": 96}),
        ("shipping-service", "REQUEST_TIMEOUT", {"dependency": "carrier-api"}),
        ("search-ui", "HEALTHCHECK_FAIL", {}),
    ]
    for i, (source, event_type, metadata) in enumerate(unrelated):
        raw = RawTelemetryEvent(
            timestamp=(T0 + timedelta(seconds=10 * i)).isoformat(),
            source=source,
            event_type=event_type,
            metadata=metadata,
        )
        use_case.ingest(adapter.translate(raw))

    assert [e for e in graph.edges if e.edge_type is EdgeType.CAUSAL] == []
    assert compute_incident_severity(graph) != "Critical"


def _ev(n: int) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.UUID(int=n),
        event_type=CoreEventType.STATE_CHANGE,
        severity=Severity.ERROR,
        timestamp=T0,
        related_component="test",
        description="test",
    )


def _use_case(graph: IncidentGraph, *rules: CorrelationRuleFn) -> CorrelateEventsUseCase:
    return CorrelateEventsUseCase(graph, SlidingWindow(), CorrelationConfig(), rules=list(rules))


def test_a_root_anomaly_adds_a_node_without_edges() -> None:
    graph = IncidentGraph()
    use_case = _use_case(graph, lambda e, w, c, t: [RuleMatch(trigger=e, cause=None, rule_id="R")])

    use_case.ingest(_ev(1))

    assert len(graph.nodes) == 1
    assert graph.edges == []


def test_re_ingesting_an_event_adds_no_duplicate_nodes_or_edges() -> None:
    graph = IncidentGraph()
    cause, effect = _ev(1), _ev(2)

    def link(event: CoreEvent, *_: object) -> list[RuleMatch]:
        return [RuleMatch(trigger=effect, cause=cause, rule_id="LINK")] if event == effect else []

    use_case = _use_case(graph, link)
    use_case.ingest(cause)
    use_case.ingest(effect)
    use_case.ingest(effect)

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1


def test_two_rules_linking_the_same_pair_each_keep_their_edge() -> None:
    # Edge identity is (source, target, type, rule_id): each rule's evidence is kept.
    graph = IncidentGraph()
    cause, effect = _ev(1), _ev(2)

    def rule(rule_id: str) -> CorrelationRuleFn:
        def match(event: CoreEvent, *_: object) -> list[RuleMatch]:
            return (
                [RuleMatch(trigger=effect, cause=cause, rule_id=rule_id)] if event == effect else []
            )

        return match

    use_case = _use_case(graph, rule("RULE_A"), rule("RULE_B"))
    use_case.ingest(cause)
    use_case.ingest(effect)

    assert sorted(e.rule_id or "" for e in graph.edges) == ["RULE_A", "RULE_B"]
