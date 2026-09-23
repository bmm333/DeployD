from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from deployd.application.dtos.diagnosis import DiagnosisTier, TierDiagnosisResult, TierRemediation
from deployd.application.dtos.investigation_request import InvestigationRequest
from deployd.application.dtos.retrieval import RetrievalResult
from deployd.application.use_cases.build_investigation import BuildInvestigation
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity
from deployd.domain.graph.edge_type import EdgeType
from deployd.domain.health.process_state import ProcessHealthStatus
from deployd.domain.health.tracker import ComponentHealthTracker


@pytest.fixture
def mock_orchestrator() -> Mock:
    orchestrator = Mock()
    orchestrator.run.return_value = TierDiagnosisResult(
        tier=DiagnosisTier.CHAIN_ONLY,
        fsm_state=ProcessHealthStatus.CRASHING,
        causal_chains=[],
        remediation=TierRemediation(summary="Test", requires_human_approval=True),
    )
    return orchestrator


@pytest.fixture
def mock_retrieval() -> Mock:
    retrieval = Mock()
    retrieval.execute.return_value = RetrievalResult(candidates=[])
    return retrieval


def test_build_investigation_success(mock_orchestrator: Mock, mock_retrieval: Mock) -> None:
    # Arrange
    use_case = BuildInvestigation(orchestrator=mock_orchestrator, retrieval_use_case=mock_retrieval)

    dt = datetime.now(timezone.utc)
    events = [
        CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=dt,
            severity=Severity.CRITICAL,
            description="Crash 1",
            metadata={},
        ),
        CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=dt,
            severity=Severity.CRITICAL,
            description="Crash 2",
            metadata={"causal_parent_index": 0},  # Should create CAUSAL edge to index 0
        ),
        CoreEvent(
            event_type=CoreEventType.HEALTH_CHECK_FAIL,
            timestamp=dt,
            severity=Severity.ERROR,
            description="Failed",
            metadata={},  # Should create TEMPORAL edge to index 1
        ),
    ]

    result = use_case.execute(component_name="auth-service", events=events)
    assert result.tier == DiagnosisTier.CHAIN_ONLY
    mock_retrieval.execute.assert_called_once_with(query="auth-service: PROCESS_CRASH")
    mock_orchestrator.run.assert_called_once()
    request: InvestigationRequest = mock_orchestrator.run.call_args[0][0]
    assert request.component == "auth-service"
    assert request.fsm_state in [ProcessHealthStatus.CRASHING, ProcessHealthStatus.CRASH_LOOP]
    edges = request.graph.edges
    assert len(edges) == 2
    assert edges[0].edge_type == EdgeType.CAUSAL
    assert edges[0].confidence == 1.0
    assert edges[1].edge_type == EdgeType.TEMPORAL
    assert edges[1].confidence == 0.5


def test_build_investigation_on_event_triggers(
    mock_orchestrator: Mock, mock_retrieval: Mock
) -> None:
    tracker = ComponentHealthTracker(cooldown_window_s=300)
    use_case = BuildInvestigation(
        orchestrator=mock_orchestrator, retrieval_use_case=mock_retrieval, health_tracker=tracker
    )

    dt = datetime.now(timezone.utc)
    event = CoreEvent(
        event_type=CoreEventType.PROCESS_CRASH,
        timestamp=dt,
        severity=Severity.CRITICAL,
        description="Crash",
        related_component="test-comp",
    )

    result = use_case.on_event(event)

    # orchestrator run should be called
    mock_orchestrator.run.assert_called_once()

    # Ensure precalculated fsm_state is used
    request: InvestigationRequest = mock_orchestrator.run.call_args[0][0]
    assert request.fsm_state == ProcessHealthStatus.CRASHING
    assert request.component == "test-comp"
    assert result is not None


def test_build_investigation_on_event_cooldown(
    mock_orchestrator: Mock, mock_retrieval: Mock
) -> None:
    tracker = ComponentHealthTracker(cooldown_window_s=300)
    use_case = BuildInvestigation(
        orchestrator=mock_orchestrator, retrieval_use_case=mock_retrieval, health_tracker=tracker
    )

    dt = datetime.now(timezone.utc)
    # First crash triggers
    use_case.on_event(
        CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=dt,
            severity=Severity.CRITICAL,
            description="Crash",
            related_component="test-comp",
        )
    )
    assert mock_orchestrator.run.call_count == 1

    # Second crash within cooldown does NOT trigger
    result = use_case.on_event(
        CoreEvent(
            event_type=CoreEventType.PROCESS_CRASH,
            timestamp=dt + timedelta(seconds=100),
            severity=Severity.CRITICAL,
            description="Crash 2",
            related_component="test-comp",
        )
    )

    assert mock_orchestrator.run.call_count == 1
    assert result is None
