import json

import pytest
from demo.ui_logic import (
    Source,
    choose_source,
    describe_events,
    parse_events,
    uncovered_chains,
)

EVENT = {"timestamp": "2026-09-30T10:00:00Z", "source": "auth-service", "event_type": "LATENCY"}


# ── parsing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload", [[EVENT], {"events": [EVENT]}, EVENT], ids=["list", "wrapped", "single"]
)
def test_the_accepted_shapes(payload: object) -> None:
    assert parse_events(json.dumps(payload)) == [EVENT]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "no events"),
        ('["x"]', "list of event objects"),
        ('[{"source": "a"}]', "missing required field"),
        ("{}", "missing required field"),
    ],
)
def test_what_is_refused_and_why(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_events(raw)


def test_invalid_json_is_a_json_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_events("not json")


# ── what will be sent ─────────────────────────────────────────────────────────


def test_an_uploaded_file_wins_and_a_pasted_text_is_flagged_as_ignored() -> None:
    assert choose_source("a.json", "[...]", "") == Source("a.json", "[...]", ignored_paste=False)
    assert choose_source("a.json", "[...]", "  {} ") == Source(
        "a.json", "[...]", ignored_paste=True
    )


def test_pasted_text_is_used_when_there_is_no_file_and_blank_means_nothing() -> None:
    assert choose_source(None, None, " [x] ") == Source("pasted text", "[x]")
    assert choose_source(None, None, "   \n") is None


def test_describe_events_names_the_components_without_repeating_them() -> None:
    events = [{"source": "a"}, {"source": "b"}, {"source": "a"}]

    assert describe_events(events) == "3 events · a, b"
    assert describe_events(events[:1]) == "1 event · a"


def test_describe_events_shortens_a_long_list_of_components() -> None:
    events = [{"source": f"svc-{i}"} for i in range(6)]

    assert describe_events(events) == "6 events · svc-0, svc-1, svc-2, svc-3 +2"


# ── chains an investigation left out ──────────────────────────────────────────


def _graph(*chains: list[str], temporal: tuple[str, str] | None = None) -> dict:
    nodes, edges = [], []
    for chain in chains:
        ids = [f"{chain[0]}-{i}" for i in range(len(chain))]
        nodes += [{"id": i, "source": s} for i, s in zip(ids, chain, strict=True)]
        edges += [
            {"source": a, "target": b, "relationship": "CAUSAL"}
            for a, b in zip(ids, ids[1:], strict=False)
        ]
    if temporal:
        edges.append({"source": temporal[0], "target": temporal[1], "relationship": "TEMPORAL"})
    return {"nodes": nodes, "edges": edges}


def test_a_second_chain_with_other_components_is_reported() -> None:
    graph = _graph(
        ["analytics-postgres", "reporting-service", "dashboard-ui"],
        ["elasticsearch-cluster", "search-service", "api-gateway"],
    )

    assert uncovered_chains(graph, ["analytics-postgres", "reporting-service"]) == [
        ["elasticsearch-cluster", "search-service", "api-gateway"]
    ]


def test_the_investigated_chain_alone_has_nothing_left_out() -> None:
    graph = _graph(["a", "b", "c"])

    assert uncovered_chains(graph, ["a", "b", "c"]) == []


def test_a_chain_sharing_a_component_with_the_investigation_counts_as_covered() -> None:
    graph = _graph(["a", "b", "c"], ["b", "x", "y"])

    assert uncovered_chains(graph, ["a", "b", "c"]) == []


def test_isolated_anomalies_and_temporal_links_are_not_chains() -> None:
    graph = _graph(["a", "b", "c"])
    graph["nodes"].append({"id": "lonely", "source": "z"})
    graph["edges"].append({"source": "a-0", "target": "lonely", "relationship": "TEMPORAL"})

    assert uncovered_chains(graph, ["a", "b", "c"]) == []


def test_an_empty_graph_has_no_chains() -> None:
    assert uncovered_chains({"nodes": [], "edges": []}, []) == []
    assert uncovered_chains({}, ["a"]) == []
