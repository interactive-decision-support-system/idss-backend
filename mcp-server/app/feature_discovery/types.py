"""Pydantic types for the feature-discovery prototype."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["reddit", "seed"]


class UserQuery(BaseModel):
    """One piece of shopper-authored text scoped to a product category.

    Intentionally loose on fields so all three source adapters can
    populate the same record. `text` is whatever the shopper wrote; the
    extractor runs off this plus `product_type`.
    """

    source: SourceKind
    source_url: str | None = None
    source_id: str
    product_type: str
    text: str
    author: str | None = None
    fetched_at: datetime


class ExtractedFeatures(BaseModel):
    """One query after LLM feature extraction.

    The four buckets mirror the merchant-agent contract so downstream
    gap analysis lines up cleanly with `StructuredQuery`
    (hard_filters / soft_preferences / user_context).
    """

    source_id: str
    product_type: str
    mentioned_attributes: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    implicit_concerns: list[str] = Field(default_factory=list)
    model: str | None = None


class FeatureFrequency(BaseModel):
    """Aggregated frequency of one feature across a corpus of queries."""

    key: str
    bucket: Literal[
        "mentioned_attribute",
        "use_case",
        "hard_constraint",
        "soft_preference",
        "implicit_concern",
    ]
    count: int
    fraction: float
    example_source_ids: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    """Gap analysis between discovered features and catalog enrichment."""

    product_type: str
    total_queries: int
    catalog_sample_size: int
    covered: list[FeatureFrequency] = Field(default_factory=list)
    missing: list[FeatureFrequency] = Field(default_factory=list)
    underused: list[str] = Field(default_factory=list)
