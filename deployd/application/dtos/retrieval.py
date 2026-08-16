"""
DD-1
DTOs for ChromaDB retrieval results.

``RetrievedEvidence``  — one ranked historical incident returned by the RAG agent.
``EvidenceReference``  — a compact citation used inside DiagnosisResult to link claims to evidence.

Neither class leaks ChromaDB internals (Documents, QueryResults, etc.) across the boundary.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RetrievedEvidence(BaseModel):
    """
    A single historical incident retrieved from ChromaDB and ranked by the RAG pipeline.

    All numeric score fields are validated as floats in [0.0, 1.0].
    ``final_score`` is the blended rank used to order results presented to the agent.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(
        ...,
        description="Unique identifier of the historical incident in the vector store.",
        examples=["INC-20240701-0007"],
    )
    final_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Blended relevance score after combining all ranking dimensions (0.0–1.0).",
        examples=[0.87],
    )
    semantic_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine-similarity score from the embedding-based semantic search (0.0–1.0).",
        examples=[0.91],
    )
    causal_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score reflecting alignment between causal chains (0.0–1.0).",
        examples=[0.78],
    )
    temporal_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score derived from temporal proximity of events in the incident (0.0–1.0).",
        examples=[0.65],
    )
    component_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score based on overlap between affected components (0.0–1.0).",
        examples=[0.80],
    )
    dependency_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score derived from dependency-graph topology match (0.0–1.0).",
        examples=[0.72],
    )
    matched_causal_chain: list[str] = Field(
        default_factory=list,
        description="Ordered list of event types that form the matched causal chain.",
        examples=[["DEPLOY_STARTED", "DEPENDENCY_FAILURE", "PROCESS_CRASH"]],
    )
    matched_nodes: list[str] = Field(
        default_factory=list,
        description="Component names that were part of the matched graph sub-structure.",
        examples=[["api-gateway", "auth-service"]],
    )
    historical_root_cause: Optional[str] = Field(
        default=None,
        description="Root cause conclusion recorded for the matched historical incident.",
        examples=["OOMKill caused by memory leak in auth-service v2.3.1"],
    )
    historical_fix: Optional[str] = Field(
        default=None,
        description="Fix that resolved the matched historical incident.",
        examples=["Rolled back auth-service to v2.2.9 and applied memory limit patch."],
    )
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Traceability metadata (e.g. ChromaDB document ID, collection name).",
    )
    runbook_id: Optional[str] = Field(
        default=None,
        description="ID of the runbook associated with the historical incident, if any.",
        examples=["RB-AUTH-SERVICE-OOMKILL"],
    )


class EvidenceReference(BaseModel):
    """
    A compact citation linking a diagnosis claim to a specific retrieved incident.

    Included in ``DiagnosisResult`` so that every agent assertion is traceable.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(
        ...,
        description="ID of the historical incident being referenced.",
        examples=["INC-20240701-0007"],
    )
    relevance_explanation: str = Field(
        ...,
        min_length=1,
        description="Plain-language explanation of why this incident is relevant to the current diagnosis.",
        examples=["Same OOMKill pattern on auth-service observed during peak traffic last July."],
    )
    similarity_scores: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Per-dimension similarity scores keyed by dimension name. "
            "All values must be in [0.0, 1.0] — enforced by the mapper before construction."
        ),
        examples=[{"semantic": 0.91, "causal": 0.78, "component": 0.80}],
    )
