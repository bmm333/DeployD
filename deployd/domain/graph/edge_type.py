from enum import Enum


class EdgeType(str, Enum):
    TEMPORAL = "TEMPORAL"
    CAUSAL = "CAUSAL"
    DEPENDENCY = "DEPENDENCY"
    OBSERVED_AS = "OBSERVED_AS"
