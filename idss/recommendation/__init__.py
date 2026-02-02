"""
Recommendation methods for IDSS.

Two ranking methods available:
- embedding_similarity: Dense Vector + MMR diversification
- coverage_risk: Coverage-Risk Optimization with phrase alignment
"""
from idss.recommendation.embedding_similarity import rank_with_embedding_similarity
from idss.recommendation.coverage_risk import rank_with_coverage_risk

__all__ = [
    "rank_with_embedding_similarity",
    "rank_with_coverage_risk",
]
