"""
IDSS - Interactive Decision Support System

A simplified system for vehicle recommendations with:
- Configurable k (number of questions)
- LLM-based impatience detection
- Entropy-based diversification
"""

from idss.core.controller import IDSSController, IDSSResponse, create_controller
from idss.core.config import IDSSConfig, get_config, set_config

__all__ = [
    'IDSSController',
    'IDSSResponse',
    'create_controller',
    'IDSSConfig',
    'get_config',
    'set_config',
]

__version__ = '0.1.0'
