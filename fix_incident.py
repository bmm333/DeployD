with open("deployd/domain/incident/incident.py") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if (
        'graph_snapshot: dict[str, object] = Field(default_factory=lambda: {"nodes": [], "edges": []}'
        in line
    ):
        # replace the whole line cleanly
        lines[i] = (
            '    graph_snapshot: dict[str, object] = Field(default_factory=lambda: {"nodes": [], "edges": []})  # type: ignore[dict-item]\n'
        )

with open("deployd/domain/incident/incident.py", "w") as f:
    f.writelines(lines)
