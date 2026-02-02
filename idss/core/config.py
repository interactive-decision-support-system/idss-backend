"""
Configuration management for IDSS.

Loads settings from YAML config file and provides typed access.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()


def _project_root() -> Path:
    """Return project root (parent of idss package)."""
    return Path(__file__).resolve().parent.parent.parent


DEFAULT_CONFIG_PATH = _project_root() / "config" / "default.yaml"


@dataclass
class IDSSConfig:
    """Configuration for the IDSS system."""

    # Core parameters
    k: int = 3                          # Number of questions to ask (0 = skip interview)
    n_vehicles_per_row: int = 3         # Vehicles per output row
    num_rows: int = 3                   # Number of output rows (diversification buckets)

    # Recommendation method
    recommendation_method: str = "coverage_risk"  # "embedding_similarity" or "coverage_risk"

    # Embedding Similarity parameters
    embedding_similarity_lambda_param: float = 0.85
    embedding_similarity_cluster_size: int = 3
    embedding_similarity_min_similarity: float = 0.4

    # Coverage-Risk parameters
    coverage_risk_lambda_risk: float = 0.5
    coverage_risk_mode: str = "max"
    coverage_risk_tau: float = 0.5
    coverage_risk_alpha: float = 1.0

    # Ablation flags (for experiments)
    use_mmr_diversification: bool = True      # Use MMR in embedding similarity ranking
    use_entropy_bucketing: bool = True        # Use entropy-based bucketing for diversification
    use_progressive_relaxation: bool = False   # Use progressive filter relaxation
    use_entropy_questions: bool = True        # Use entropy-based question dimension selection

    # Model configuration
    semantic_parser_model: str = "gpt-4o-mini"
    question_generator_model: str = "gpt-4o"
    temperature: float = 0

    # Data paths
    vehicle_db: str = "data/car_dataset_idss/uni_vehicles.db"
    faiss_index_dir: str = "data/car_dataset_idss/faiss_indices"
    phrase_embeddings_dir: str = "data/car_dataset_idss/phrase_embeddings"
    reviews_db: str = "data/car_dataset_idss/vehicle_reviews_tavily.db"

    @classmethod
    def from_yaml(cls, config_path: Optional[Path] = None) -> "IDSSConfig":
        """Load configuration from YAML file."""
        path = config_path or DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()

        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}

        idss_config = data.get('idss', {})
        recommendation_config = data.get('recommendation', {})
        models_config = data.get('models', {})
        data_config = data.get('data', {})

        embedding_similarity = recommendation_config.get('embedding_similarity', {})
        coverage_risk = recommendation_config.get('coverage_risk', {})

        return cls(
            k=idss_config.get('k', 3),
            n_vehicles_per_row=idss_config.get('n_vehicles_per_row', 3),
            num_rows=idss_config.get('num_rows', 3),
            recommendation_method=recommendation_config.get('method', 'coverage_risk'),
            embedding_similarity_lambda_param=embedding_similarity.get('lambda_param', 0.85),
            embedding_similarity_cluster_size=embedding_similarity.get('cluster_size', 3),
            embedding_similarity_min_similarity=embedding_similarity.get('min_similarity', 0.4),
            coverage_risk_lambda_risk=coverage_risk.get('lambda_risk', 0.5),
            coverage_risk_mode=coverage_risk.get('mode', 'max'),
            coverage_risk_tau=coverage_risk.get('tau', 0.5),
            coverage_risk_alpha=coverage_risk.get('alpha', 1.0),
            semantic_parser_model=models_config.get('semantic_parser', 'gpt-4o-mini'),
            question_generator_model=models_config.get('question_generator', 'gpt-4o'),
            temperature=models_config.get('temperature', 0),
            vehicle_db=data_config.get('vehicle_db', 'data/car_dataset_idss/uni_vehicles.db'),
            faiss_index_dir=data_config.get('faiss_index_dir', 'data/car_dataset_idss/faiss_indices'),
            phrase_embeddings_dir=data_config.get('phrase_embeddings_dir', 'data/car_dataset_idss/phrase_embeddings'),
            reviews_db=data_config.get('reviews_db', 'data/car_dataset_idss/vehicle_reviews_tavily.db'),
        )


# Global config instance
_config: Optional[IDSSConfig] = None


def get_config() -> IDSSConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = IDSSConfig.from_yaml()
    return _config


def set_config(config: IDSSConfig) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
