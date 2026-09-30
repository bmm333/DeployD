with open("deployd/domain/causal/causal_rule.py") as f:
    content = f.read()

# Add _status_code helper
helper = """
def _status_code(event: CoreEvent, config: CorrelationConfig) -> int | None:
    val = config.get_metric(event.metadata, "status_code")
    if isinstance(val, int):
        return val
    return None

def rule_db_latency_anomaly("""

content = content.replace("def rule_db_latency_anomaly(", helper)

# Update list comprehension
old_cluster = """    cluster = [
        e
        for e in window
        if e.related_component == source
        and isinstance(config.get_metric(e.metadata, "status_code"), int)
        and config.get_metric(e.metadata, "status_code") >= 500
    ]"""

new_cluster = """    cluster = [
        e
        for e in window
        if e.related_component == source
        and (_status_code(e, config) or 0) >= 500
    ]"""

content = content.replace(old_cluster, new_cluster)

with open("deployd/domain/causal/causal_rule.py", "w") as f:
    f.write(content)

with open("deployd/domain/incident/incident.py") as f:
    content = f.read()

content = content.replace("type: ignore[misc]", "type: ignore[explicit-any]")
content = content.replace(
    'lambda: {"nodes": [], "edges": []}',
    'lambda: {"nodes": [], "edges": []}  # type: ignore[dict-item]',
)

with open("deployd/domain/incident/incident.py", "w") as f:
    f.write(content)

with open("deployd/domain/causal/config.py") as f:
    content = f.read()

content = content.replace("type: ignore[misc]", "type: ignore[explicit-any]")

with open("deployd/domain/causal/config.py", "w") as f:
    f.write(content)
