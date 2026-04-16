"""
Re-export of the shopping↔merchant contract.

The canonical definitions live in `mcp-server/app/contract.py`. This module
re-exports them so `shopping_agent_llm` code depends on the contract
symbolically and a future file move doesn't cascade.

If the contract relocates (per ARCHITECTURE.md it is targeted to
`merchant_agent/contract.py`), update this file only.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MCP_APP = Path(__file__).resolve().parent.parent / "mcp-server"
if str(_MCP_APP) not in sys.path:
    sys.path.insert(0, str(_MCP_APP))

from app.contract import Offer, StructuredQuery  # noqa: E402
from app.schemas import ProductSummary  # noqa: E402

__all__ = ["StructuredQuery", "Offer", "ProductSummary"]
