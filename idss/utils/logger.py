"""
Logging configuration for IDSS.

Provides a centralized logger that can be configured via environment variables.
"""
import logging
import os
import sys

# Get log level from environment variable (default: INFO)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("idss")
logger.setLevel(LOG_LEVEL)

if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(LOG_LEVEL)

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

# Prevent propagation to root logger (avoid duplicate logs)
logger.propagate = False


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Optional name for the logger (will be appended to 'idss')

    Returns:
        Logger instance
    """
    if name:
        return logging.getLogger(f"idss.{name}")
    return logger
