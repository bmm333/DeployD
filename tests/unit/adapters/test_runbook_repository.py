from deployd.adapters.outgoing.vector_store.runbook_repository import (
    JSONRunbookRepository,
    RunbookDetail,
)


class TestJSONRunbookRepository:
    def test_loads_runbooks_from_directory(self) -> None:
        repo = JSONRunbookRepository()
        runbooks = repo.list_all()
        assert len(runbooks) >= 10

    def test_get_by_id_returns_correct_detail(self) -> None:
        repo = JSONRunbookRepository()
        detail = repo.get_by_id("RB-AUTH-SERVICE-OOMKILL")
        assert detail is not None
        assert isinstance(detail, RunbookDetail)
        assert detail.runbook_id == "RB-AUTH-SERVICE-OOMKILL"
        assert detail.incident_id == "INC-2026-08-27-001"
        assert "auth-service" in detail.affected_components
        assert "RESOURCE_EXHAUSTION" in detail.causal_chain
        assert detail.root_cause != ""
        assert detail.fix != ""

    def test_get_by_id_missing_returns_none(self) -> None:
        repo = JSONRunbookRepository()
        assert repo.get_by_id("NON-EXISTENT-ID") is None
