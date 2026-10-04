"""Unit tests for AgnoGroqAgent run-output helpers (no LLM calls)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agno.run.base import RunStatus
from deployd.adapters.outgoing.ai.agno_agent import _raise_on_failed_run, _total_tokens


def test_failed_run_raises_with_provider_message() -> None:
    response = SimpleNamespace(status=RunStatus.error, content='{"error": "rate_limit_exceeded"}')

    with pytest.raises(RuntimeError, match="rate_limit_exceeded"):
        _raise_on_failed_run(response)


def test_completed_run_passes() -> None:
    _raise_on_failed_run(SimpleNamespace(status=RunStatus.completed, content="ok"))


def test_total_tokens_reads_metrics_or_returns_none() -> None:
    assert _total_tokens(SimpleNamespace(metrics=SimpleNamespace(total_tokens=412))) == 412
    assert _total_tokens(SimpleNamespace(metrics=None)) is None
    assert _total_tokens(SimpleNamespace()) is None
