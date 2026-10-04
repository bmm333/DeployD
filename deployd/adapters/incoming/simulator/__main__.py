"""DID-16: CLI entrypoint for the EventSimulator.

Usage
-----
    python -m deployd.adapters.incoming.simulator <scenario_name>

Where ``<scenario_name>`` is the JSON filename (without extension) inside
``data/scenarios/``.  For example:

    python -m deployd.adapters.incoming.simulator oom_kill_auth_service
    python -m deployd.adapters.incoming.simulator novel_network_partition

Exit codes
----------
0 — Success (including when tier does not match ``expected_tier``)
1 — Scenario file not found or unrecoverable error
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger("deployd.simulator")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DATA_DIR = _REPO_ROOT / "data"
_SCENARIOS_DIR = _DATA_DIR / "scenarios"


def _print_separator(char: str = "─", width: int = 60) -> None:
    print(char * width)


def main(scenario_name: str) -> int:
    from deployd.adapters.incoming.simulator.event_simulator import EventSimulator

    scenario_path = _SCENARIOS_DIR / f"{scenario_name}.json"
    if not scenario_path.exists():
        available = sorted(p.stem for p in _SCENARIOS_DIR.glob("*.json"))
        print(f"\n[ERROR] Scenario not found: {scenario_path}")
        print(f"Available scenarios: {available}")
        return 1

    print(f"\n{'═' * 60}")
    print("  DeployD — Scenario Simulator")
    print(f"{'═' * 60}")
    print(f"  Scenario : {scenario_name}")

    simulator = EventSimulator(data_dir=_DATA_DIR)
    result = simulator.run(scenario_path)

    _print_separator()
    print(f"[MODE: {result.agent_mode}]")
    _print_separator()

    diagnosis = result.diagnosis
    remediation = diagnosis.remediation
    structured = diagnosis.structured_diagnosis

    print(f"Tier            : {diagnosis.tier.value}")
    print(f"FSM State       : {diagnosis.fsm_state.value}")
    print(f"Causal chains   : {len(diagnosis.causal_chains)}")
    print(f"Expected tier   : {result.expected_tier}")

    tier_match = diagnosis.tier.value == result.expected_tier
    status = "✅ MATCH" if tier_match else "❌ MISMATCH"
    print(f"Tier check      : {status}")

    _print_separator()
    print("Remediation:")
    print(f"  Summary                : {remediation.summary}")
    print(f"  Requires human approval: {remediation.requires_human_approval}")
    if remediation.evidence_references:
        print(f"  Evidence references    : {', '.join(remediation.evidence_references)}")

    if structured is not None:
        _print_separator()
        print("Agent Diagnosis:")
        print(f"  Root cause     : {structured.root_cause}")
        print(f"  Confidence     : {structured.confidence}")
        print(f"  Recommendation : {structured.recommendation}")
        print("  Reasoning      :")
        for line in structured.reasoning.splitlines():
            print(f"    {line}")

    _print_separator("═")
    print()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m deployd.adapters.incoming.simulator <scenario_name>")
        sys.exit(1)

    sys.exit(main(sys.argv[1]))
