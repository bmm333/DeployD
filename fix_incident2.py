with open("deployd/domain/incident/incident.py") as f:
    content = f.read()

content = content.replace(
    'lambda: {"nodes": [], "edges": []})  # type: ignore[dict-item]',
    'lambda: {"nodes": [], "edges": []})  # type: ignore[arg-type]',
)

with open("deployd/domain/incident/incident.py", "w") as f:
    f.write(content)
