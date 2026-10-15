"""The read-only lookups the MCP server relies on (DID-40), exercised on their own."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from deployd.adapters.outgoing.vector_store.runbook_repository import JSONRunbookRepository
from deployd.application.dtos.retrieval import RetrievalCandidate, RetrievalResult
from deployd.entrypoints import api
from fastapi.testclient import TestClient

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    retrieval = Mock()
    retrieval.retriever.retrieve.return_value = RetrievalResult(
        candidates=[RetrievalCandidate("RB-AUTH-SERVICE-OOMKILL", 0.9)], confidence_threshold=0.5
    )
    retrieval.repo = JSONRunbookRepository(Path("data/runbooks"))
    monkeypatch.setattr(api, "_get_retrieval", lambda: retrieval)


def test_runbook_search_returns_the_matches_with_their_score_and_the_threshold() -> None:
    response = client.get("/api/v1/runbooks/search", params={"query": "auth service memory"})

    body = response.json()
    assert response.status_code == 200
    assert body["threshold"] == 0.5
    assert body["runbooks"][0]["runbook_id"] == "RB-AUTH-SERVICE-OOMKILL"
    assert body["runbooks"][0]["score"] == 0.9


@pytest.mark.parametrize("query", ["", "ab", "x" * 501])
def test_runbook_search_refuses_a_query_outside_the_length_limits(query: str) -> None:
    assert client.get("/api/v1/runbooks/search", params={"query": query}).status_code == 422


def test_compatibility_of_a_declared_component() -> None:
    response = client.get("/api/v1/components/payment-service/compatibility")

    assert response.status_code == 200
    assert response.json()["component"] == "payment-service"


def test_compatibility_of_an_unknown_component_is_a_404() -> None:
    assert client.get("/api/v1/components/nope/compatibility").status_code == 404


def test_compatibility_refuses_a_name_that_is_not_a_component_name() -> None:
    assert client.get("/api/v1/components/Payment_Service!/compatibility").status_code == 422
