"""Unit tests for GraphStore - in memory runbook structure store"""

from deployd.adapters.outgoing.vector_store.graph_store import GraphStore, RunbookStructure


class TestGraphStoreAdd:
    def test_add_and_retrieve(self) -> None:
        store = GraphStore()
        rb = RunbookStructure(
            runbook_id="rb-1",
            causal_chain=("deploy", "crash"),
            affected_components=frozenset({"api", "db"}),
        )
        store.add(rb)
        assert store.get("rb-1") == rb

    def test_add_overwrites_same_id(self) -> None:
        store = GraphStore()
        rb1 = RunbookStructure("rb-1", ("deploy",), frozenset({"api"}))
        rb2 = RunbookStructure("rb-1", ("deploy", "crash"), frozenset({"api", "db"}))
        store.add(rb1)
        store.add(rb2)
        assert store.get("rb-1") == rb2

    def test_add_multiple_different_ids(self) -> None:
        store = GraphStore()
        store.add(RunbookStructure("rb-1", ("deploy",), frozenset({"api"})))
        store.add(RunbookStructure("rb-2", ("crash",), frozenset({"db"})))
        assert len(store.all()) == 2


class TestGraphStoreGet:
    def test_get_missing_returns_none(self) -> None:
        store = GraphStore()
        assert store.get("doesnt-exist") is None


class TestGraphStoreAll:
    def test_all_empty(self) -> None:
        store = GraphStore()
        assert store.all() == []

    def test_all_returns_list_not_dict_values(self) -> None:
        store = GraphStore()
        store.add(RunbookStructure("rb-1", ("deploy",), frozenset()))
        result = store.all()
        assert isinstance(result, list)

    def test_all_returns_copy(self) -> None:
        store = GraphStore()
        store.add(RunbookStructure("rb-1", ("deploy",), frozenset()))
        result = store.all()
        result.clear()
        assert len(store.all()) == 1
