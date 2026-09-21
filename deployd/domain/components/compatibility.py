import logging
import operator
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from deployd.domain.components.component import ComponentState, RawConstraintDict

logger = logging.getLogger(__name__)


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


@dataclass
class CompatibilityReport:
    evidence_id: str
    component_name: str
    status: CompatibilityStatus
    installed_versions: dict[str, str]
    violations: list[str]


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a semantic version string into a tuple of ints for comparison."""
    # Strip any non-numeric prefix like 'v'
    version_str = re.sub(r"^[^\d]+", "", version_str)
    parts = []
    for p in version_str.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    # Pad to at least 3 parts for standard semver comparison
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _check_constraint(installed_version: str, constraint: str) -> bool:
    """Evaluate a version constraint like '>=2.0.0' against an installed version like '1.10.14'."""
    match = re.match(r"(>=|<=|>|<|==|!=)?\s*(.+)", constraint.strip())
    if not match:
        logger.warning("Unparseable constraint: %s", constraint)
        return True  # Fail-open for unparseable constraints

    op_str, target_version = match.groups()
    op_str = op_str or "=="

    ops: dict[str, Callable[[tuple[int, ...], tuple[int, ...]], bool]] = {
        ">=": operator.ge,
        "<=": operator.le,
        ">": operator.gt,
        "<": operator.lt,
        "==": operator.eq,
        "!=": operator.ne,
    }

    op_func = ops.get(op_str, operator.eq)

    inst_tuple = _parse_version(installed_version)
    target_tuple = _parse_version(target_version)

    return bool(op_func(inst_tuple, target_tuple))  # type: ignore[no-untyped-call]


class CompatibilityEngine:
    """Deterministic dependency constraints evaluator."""

    def __init__(self, constraints_db: dict[str, dict[str, RawConstraintDict]]):
        """
        Args:
            constraints_db: A dictionary mapping package_name -> package_version -> RawConstraintDict
        """
        self._constraints_db = constraints_db

    def evaluate(self, state: ComponentState) -> CompatibilityReport:
        """Evaluate a component's installed packages against known constraints."""
        installed = state.all_packages
        violations: list[str] = []
        has_known_rules = False

        for pkg, version in installed.items():
            # Do we have rules for this package?
            if pkg not in self._constraints_db:
                continue

            # Do we have rules for this specific version?
            pkg_rules = self._constraints_db[pkg]
            if version not in pkg_rules:
                continue

            has_known_rules = True
            rules = pkg_rules[version]

            # Check 'requires'
            for req_pkg, req_constraint in rules.get("requires", {}).items():
                if req_pkg not in installed:
                    violations.append(
                        f"{pkg} {version} requires {req_pkg} {req_constraint}, but it is missing"
                    )
                elif not _check_constraint(installed[req_pkg], req_constraint):
                    violations.append(
                        f"{pkg} {version} requires {req_pkg} {req_constraint}, found {installed[req_pkg]}"
                    )

            # Check 'conflicts'
            for conf_pkg, conf_constraint in rules.get("conflicts", {}).items():
                if conf_pkg in installed and _check_constraint(
                    installed[conf_pkg], conf_constraint
                ):
                    violations.append(
                        f"{pkg} {version} conflicts with {conf_pkg} {conf_constraint}, found {installed[conf_pkg]}"
                    )

        if violations:
            status = CompatibilityStatus.INCOMPATIBLE
        elif has_known_rules:
            status = CompatibilityStatus.COMPATIBLE
        else:
            status = CompatibilityStatus.UNKNOWN

        return CompatibilityReport(
            evidence_id=f"compat-{state.component_name}-001",
            component_name=state.component_name,
            status=status,
            installed_versions=installed,
            violations=violations,
        )
