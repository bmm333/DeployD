"""
DID-9: Detection layer.

A DetectionRule infers relationships or anomalies from evidence that has already
been observed, combined with reference/expected data that has already been supplied
to it. See ADR-005 for placement decisions.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from deployd.domain.entities.core_event import CoreEvent  # noqa: TCH001


@runtime_checkable
class DetectionRule(Protocol):
    """
    Deterministic function of (observed evidence, reference/expected data) -> zero or
    more new CoreEvents.

    Rules must be:
    - deterministic: no randomness, no AI/LLM calls, no network or disk I/O.
    - non-fabricating: only combine what's explicitly present in `evidence` and
      `reference`; never invent evidence, components, or relationships that weren't
      supplied.
    - side-effect free: rules return candidate CoreEvents for a caller to add to the
      graph; they never mutate the IncidentGraph, or their own inputs.
    """

    rule_id: str

    def evaluate(self, evidence: CoreEvent, reference: Any) -> list[CoreEvent]:  # type: ignore[misc]
        """Evaluate this rule against a single observed evidence event and reference
        data, returning zero or more new CoreEvents this rule infers.

        Returns an empty list when the rule doesn't apply to this evidence, when
        required evidence/reference data is missing, or when no anomaly is found.
        """
        ...
