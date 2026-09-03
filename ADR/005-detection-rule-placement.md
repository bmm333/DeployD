# ADR 005: DetectionRule Placement (domain/ vs application/)

## Status
Accepted

## Context
We need to decide where `DetectionRule` and its implementations (e.g., `ConfigDriftRule`) belong in the architecture.

A `DetectionRule` evaluates an observed piece of evidence (a `CoreEvent`) against some external expectation or reference data (e.g., a declared expected topology).

The reference data itself needs to be fetched from somewhere (e.g., read from a config file, queried from a database, or retrieved from a service registry).

## Decision
1. **`DetectionRule` lives in `domain/`, not `application/`.**
2. A rule must be a pure, deterministic function of exactly two already-supplied inputs: the observed evidence (`CoreEvent`) and the reference data handed to it as a plain parameter.
3. Rules perform **no I/O of their own**. They do not read config files, query databases, or call live service registries.
4. **Fetching the reference data** and deciding when to invoke a rule with it is strictly an **application-layer concern**. The `application/` layer (e.g., a use case) owns fetching the reference data and wiring it into the rule.

## Consequences
- **Positive**: Rules remain pure, dependency-free, side-effect-free, and directly unit-testable in isolation.
- **Positive**: We avoid bleeding infrastructure concerns (like network I/O or disk reads) into the domain layer.
- **Negative/Trade-off**: The application layer must be aware of what reference data a specific rule requires and orchestrate fetching it before invoking the rule. If a rule requires complex reference data fetching, the application layer carries that burden.
