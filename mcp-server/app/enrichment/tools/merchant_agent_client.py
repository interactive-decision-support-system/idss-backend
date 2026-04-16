"""Schema-handoff and proposal channel between enrichment and the merchant agent.

For v1 this is in-process: get_known_schema() composes its return value from
ORM column metadata + the registry's external strategy footprint, and
propose_schema_extension() writes a JSON file under enrichment/cache/proposals/.
The follow-up PR replaces both with a real /merchant/schema endpoint and
auto-promotion of accepted proposals into the merchant agent's filter applier.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.enrichment import registry
from app.enrichment.types import ProposalAck, ProposalDecision, SlotSchema
from app.models import Product

logger = logging.getLogger(__name__)


_PROPOSALS_DIR = Path(__file__).resolve().parents[1] / "cache" / "proposals"


# ---------------------------------------------------------------------------
# Static type inference for ORM columns
# ---------------------------------------------------------------------------

_NUMERIC_SQL_TYPES = ("NUMERIC", "INTEGER", "BIGINT", "SMALLINT")
_BOOLEAN_SQL_TYPES = ("BOOLEAN",)
_TEXT_SQL_TYPES = ("TEXT", "VARCHAR", "STRING")


def _classify_column(column) -> str:
    sql_type = str(column.type).upper()
    if any(t in sql_type for t in _NUMERIC_SQL_TYPES):
        return "numeric"
    if any(t in sql_type for t in _BOOLEAN_SQL_TYPES):
        return "boolean"
    if any(t in sql_type for t in _TEXT_SQL_TYPES):
        return "text"
    return "text"


def _slot_from_column(column) -> SlotSchema:
    return SlotSchema(
        key=column.name,
        type=_classify_column(column),
        source_strategies=["raw"],
        description=f"raw column products_default.{column.name}",
    )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def get_known_schema(merchant_id: str = "default") -> dict[str, SlotSchema]:
    """Slots the merchant agent can already filter on.

    v1 source of truth:
      - top-level columns of merchants.products_default
      - registered enrichment strategies' OUTPUT_KEYS (from app.enrichment.registry)

    What the merchant agent actually applies in its current filter code is a
    subset of this; the gap is the work tracked in the post-PR follow-up issue.
    """
    out: dict[str, SlotSchema] = {}
    for col in Product.__table__.columns:
        # Skip columns not exposed for filtering (FK metadata, timestamps).
        if col.name in {"created_at", "updated_at", "merchant_id"}:
            continue
        out[col.name] = _slot_from_column(col)

    for strategy, keys in registry.all_known_keys().items():
        for key in keys:
            if key in out:
                # Strategy key collides with a raw column — registry should
                # have rejected this. Skip rather than overwrite.
                continue
            out[key] = SlotSchema(
                key=key,
                type=_infer_strategy_key_type(key),
                source_strategies=[strategy],
                description=f"enrichment strategy {strategy}",
            )
    return out


_NUMERIC_HINT = re.compile(
    r"(_gb|_mb|_kg|_lbs|_hz|_w|_watts|_cm|_mm|_kw|_kwh|_count|_size|_score|_pages|_year|_capacity|_volume)$"
)
_BOOLEAN_HINT = re.compile(r"^(good_for_|is_|has_|supports_)")


def _infer_strategy_key_type(key: str) -> str:
    if _BOOLEAN_HINT.search(key):
        return "boolean"
    if _NUMERIC_HINT.search(key):
        return "numeric"
    return "text"


def propose_schema_extension(
    merchant_id: str,
    new_slots: list[SlotSchema],
) -> ProposalAck:
    """Record a request to extend the merchant agent's understood schema.

    v1 behavior: every novel slot is recorded as 'deferred' (awaiting the
    follow-up PR that wires the merchant agent to consume CatalogSchema).
    Slots whose key already exists in the known schema are 'accepted'
    (the merchant agent already filters on them, even if it learned about
    them via a different path).
    """
    known = get_known_schema(merchant_id)
    decisions: list[ProposalDecision] = []
    for slot in new_slots:
        if slot.key in known:
            decisions.append(
                ProposalDecision(slot=slot, decision="accepted", reason="key already known")
            )
        else:
            decisions.append(
                ProposalDecision(
                    slot=slot,
                    decision="deferred",
                    reason="merchant agent does not yet consume CatalogSchema (see follow-up issue)",
                )
            )
    proposal_id = uuid.uuid4().hex
    ack = ProposalAck(merchant_id=merchant_id, decisions=decisions, proposal_id=proposal_id)

    _PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROPOSALS_DIR / f"{merchant_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{proposal_id}.json"
    path.write_text(ack.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "schema_extension_proposed",
        extra={"merchant_id": merchant_id, "proposal_id": proposal_id, "slots": len(new_slots), "path": str(path)},
    )
    return ack
