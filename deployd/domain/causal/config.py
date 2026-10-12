"""Correlation Configuration."""

from pydantic import BaseModel, Field


class ThresholdsConfig(BaseModel):  # type: ignore
    latency_ms: int = 500
    resource_percent: int = 90
    error_rate: int = 3


class CorrelationConfig(BaseModel):  # type: ignore
    database_components: list[str] = Field(default_factory=list)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    metric_field_aliases: dict[str, list[str]] = Field(default_factory=dict)
    # How long a change event stays available to the rules (seconds of event time).
    change_lookback_seconds: int = 24 * 3600

    def get_metric(self, metadata: dict[str, object], metric_name: str) -> object | None:
        """Extract a metric value from metadata checking all aliases."""
        aliases = self.metric_field_aliases.get(metric_name, [metric_name])
        for alias in aliases:
            if alias in metadata:
                return metadata[alias]
        return None
