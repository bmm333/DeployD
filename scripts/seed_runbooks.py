from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
venv_python = ROOT / ".venv" / "bin" / "python"
if venv_python.exists() and sys.executable != str(venv_python):
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deployd.adapters.outgoing.vector_store.bm25_index import BM25RunbookIndex  # noqa: E402
from deployd.adapters.outgoing.vector_store.chroma_client import ChromaRunbookClient  # noqa: E402
from deployd.adapters.outgoing.vector_store.graph_store import (  # noqa: E402
    GraphStore,
    RunbookStructure,
)

RUNBOOKS_DIR = ROOT / "data" / "runbooks"
CHROMA_DIR = str(ROOT / "data" / "chroma")


def load_runbooks() -> list[dict]:
    files = sorted(RUNBOOKS_DIR.glob("rb_*.json"))
    if not files:
        print(f"ERROR: no runbook files found in {RUNBOOKS_DIR}")
        sys.exit(1)
    runbooks = []
    for f in files:
        with open(f) as fh:
            runbooks.append(json.load(fh))
    print(f"Loaded {len(runbooks)} runbooks from {RUNBOOKS_DIR}")
    return runbooks


def seed_chroma(runbooks: list[dict]) -> None:
    client = ChromaRunbookClient(persist_directory=CHROMA_DIR)
    for rb in runbooks:
        metadata = {
            "tags": ",".join(rb.get("tags", [])),
            "affected_components": ",".join(rb.get("affected_components", [])),
        }
        client.index_runbook(
            runbook_id=rb["runbook_id"],
            summary=rb["summary"],
            metadata=metadata,
        )
    print(f"ChromaDB seeded -> {CHROMA_DIR}")


def build_bm25(runbooks: list[dict]) -> BM25RunbookIndex:
    index = BM25RunbookIndex()
    corpus = [(rb["runbook_id"], rb["summary"]) for rb in runbooks]
    index.build(corpus)
    print(f"BM25 index built ({len(corpus)} documents)")
    return index


def build_graph_store(runbooks: list[dict]) -> GraphStore:
    store = GraphStore()
    for rb in runbooks:
        store.add(
            RunbookStructure(
                runbook_id=rb["runbook_id"],
                causal_chain=tuple(rb["causal_chain"]),
                affected_components=frozenset(rb["affected_components"]),
            )
        )
    print(f"GraphStore built ({len(runbooks)} structures)")
    return store


def main() -> None:
    runbooks = load_runbooks()
    seed_chroma(runbooks)
    build_bm25(runbooks)
    build_graph_store(runbooks)
    print("\nDone. ChromaDB is persisted; BM25 and GraphStore are verified.")
    print("Run scripts/eval_retrieval.py to measure recall@k.")


if __name__ == "__main__":
    main()
