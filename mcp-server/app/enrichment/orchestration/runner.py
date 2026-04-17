"""Run an enrichment job end-to-end.

Steps:
  1. Load N products from merchants.products_default.
  2. Run the assessor → AssessorOutput.
  3. Build a plan via the chosen orchestrator.
  4. For each product, in plan order, instantiate the agent class and call run().
     Pass cumulative agent outputs through `context` so downstream agents
     (specialist, soft_tagger) can read upstream output (taxonomy, parser).
  5. Validate each AgentResult; upsert successful ones.
  6. Optionally write a validator_v1 audit row.
  7. Build a CatalogSchema from observed parser/specialist output and propose
     extensions to the merchant agent.
  8. Return a RunSummary the CLI prints / dumps to JSON.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from sqlalchemy.orm import Session

from app.enrichment import registry
from app.enrichment.agents import validator as validator_mod
from app.enrichment.agents.assessor import Assessor, serialize as serialize_assessment
from app.enrichment.tools import db_writer, merchant_agent_client
from app.enrichment.tools.catalog_reader import load_products
from app.enrichment.tools.llm_client import get_ledger
from app.enrichment.types import (
    AgentResult,
    AssessorOutput,
    CatalogSchema,
    OrchestratorPlan,
    ProductInput,
    ProductTypeSchema,
    SlotSchema,
    StrategyOutput,
)
from app.enrichment.orchestration.fixed import FixedOrchestrator
from app.enrichment.orchestration.orchestrated import LLMOrchestrator

logger = logging.getLogger(__name__)


Mode = Literal["fixed", "orchestrated"]


# ---------------------------------------------------------------------------
# Summary record
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Bundle the runner returns: summary + assessor output + discovered schema."""

    summary: "RunSummary"
    assessment: AssessorOutput
    schema: CatalogSchema


@dataclass
class RunSummary:
    mode: Mode
    merchant_id: str
    products_processed: int
    strategies_invoked: dict[str, int] = field(default_factory=dict)
    strategies_succeeded: dict[str, int] = field(default_factory=dict)
    strategies_failed: dict[str, int] = field(default_factory=dict)
    keys_filled_per_product: list[int] = field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    schema_proposal_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "merchant_id": self.merchant_id,
            "products_processed": self.products_processed,
            "strategies_invoked": dict(self.strategies_invoked),
            "strategies_succeeded": dict(self.strategies_succeeded),
            "strategies_failed": dict(self.strategies_failed),
            "avg_keys_filled_per_product": (
                sum(self.keys_filled_per_product) / len(self.keys_filled_per_product)
                if self.keys_filled_per_product
                else 0.0
            ),
            "total_latency_ms": self.total_latency_ms,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "schema_proposal_id": self.schema_proposal_id,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_enrichment(
    db: Session,
    *,
    mode: Mode = "fixed",
    merchant_id: str = "default",
    limit: int = 10,
    strategies_filter: list[str] | None = None,
    dry_run: bool = False,
    audit: bool = False,
) -> RunResult:
    started = time.perf_counter()
    summary = RunSummary(
        mode=mode,
        merchant_id=merchant_id,
        products_processed=0,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # 1) load products
    products = load_products(db, merchant_id=merchant_id, limit=limit)
    if not products:
        summary.notes.append("no_products_loaded")
        summary.finished_at = datetime.now(timezone.utc).isoformat()
        empty_schema = CatalogSchema(
            merchant_id=merchant_id,
            generated_at=datetime.now(timezone.utc),
            catalog_size=0,
        )
        return RunResult(
            summary=summary,
            assessment=AssessorOutput(catalog_size=0),
            schema=empty_schema,
        )

    # 2) assess
    assessor = Assessor()
    assessment = assessor.assess(products)

    # Filter recommended strategies if the CLI asked for a subset.
    if strategies_filter:
        assessment.recommended_strategies = [
            s for s in assessment.recommended_strategies if s in strategies_filter
        ]

    # 3) plan
    orchestrator = FixedOrchestrator() if mode == "fixed" else LLMOrchestrator()
    plan = orchestrator.plan(products, assessment)

    # 4-5) execute + validate + write
    get_ledger().reset()
    successful_outputs_by_pid: dict[Any, list[StrategyOutput]] = defaultdict(list)
    verdicts_by_pid: dict[Any, dict[str, validator_mod.ValidationVerdict]] = defaultdict(dict)
    for product in products:
        plan_for_product = plan.per_product_agents.get(product.product_id, [])
        ctx: dict[str, Any] = {}
        keys_filled = 0
        for strategy in plan_for_product:
            result = _run_strategy(strategy, product, ctx, summary)
            verdict = validator_mod.validate(result)
            verdicts_by_pid[product.product_id][strategy] = verdict
            if result.success and verdict.passed and result.output is not None:
                successful_outputs_by_pid[product.product_id].append(result.output)
                keys_filled += len(result.output.attributes)
                # Make output available to downstream agents.
                ctx[_short(strategy)] = dict(result.output.attributes)
                summary.strategies_succeeded[strategy] = (
                    summary.strategies_succeeded.get(strategy, 0) + 1
                )
            else:
                summary.strategies_failed[strategy] = (
                    summary.strategies_failed.get(strategy, 0) + 1
                )
                if verdict.reasons:
                    logger.info(
                        "validator_rejected",
                        extra={
                            "strategy": strategy,
                            "product_id": str(product.product_id),
                            "reasons": verdict.reasons,
                        },
                    )
        summary.keys_filled_per_product.append(keys_filled)
        summary.products_processed += 1

    # write outputs (one batched commit)
    all_outputs: list[StrategyOutput] = [
        out for outs in successful_outputs_by_pid.values() for out in outs
    ]
    if audit:
        all_outputs.extend(
            validator_mod.make_audit_output(product_id=pid, verdicts=verdicts)
            for pid, verdicts in verdicts_by_pid.items()
        )
    db_writer.upsert_many(db, all_outputs, dry_run=dry_run)

    # 7) catalog schema + propose extensions
    schema = _build_catalog_schema(merchant_id, successful_outputs_by_pid, products)
    ack = merchant_agent_client.propose_schema_extension(
        merchant_id, _all_slots(schema)
    )
    summary.schema_proposal_id = ack.proposal_id

    # tally cost + latency
    summary.total_cost_usd = get_ledger().total_usd
    summary.total_latency_ms = int((time.perf_counter() - started) * 1000)
    summary.finished_at = datetime.now(timezone.utc).isoformat()

    return RunResult(summary=summary, assessment=assessment, schema=schema)


def _run_strategy(
    strategy: str,
    product: ProductInput,
    ctx: dict[str, Any],
    summary: RunSummary,
) -> AgentResult:
    summary.strategies_invoked[strategy] = summary.strategies_invoked.get(strategy, 0) + 1
    try:
        agent_cls = registry.get(strategy)
    except KeyError as exc:
        return AgentResult(
            success=False,
            output=None,
            error=str(exc),
            strategy=strategy,
            product_id=product.product_id,
        )
    return agent_cls().run(product, ctx)


def _short(strategy: str) -> str:
    """Map a strategy label to the short context key downstream agents read.
    e.g. 'taxonomy_v1' -> 'taxonomy', 'parser_v1' -> 'parsed', 'specialist_v1' -> 'specialist'.
    """
    mapping = {
        "taxonomy_v1": "taxonomy",
        "parser_v1": "parsed",
        "specialist_v1": "specialist",
        "scraper_v1": "scraped",
        "soft_tagger_v1": "soft_tagger",
    }
    return mapping.get(strategy, strategy)


# ---------------------------------------------------------------------------
# CatalogSchema synthesis
# ---------------------------------------------------------------------------


def _build_catalog_schema(
    merchant_id: str,
    outputs_by_pid: dict[Any, list[StrategyOutput]],
    products: list[ProductInput],
) -> CatalogSchema:
    """Group products by discovered product_type and tally observed slot keys.

    Each StrategyOutput contributes its top-level keys; for parsed_specs and
    scraped_specs we also drill in to record the inner spec keys.
    """
    type_to_pids: dict[str, list[Any]] = defaultdict(list)
    pid_outputs_index: dict[Any, list[StrategyOutput]] = outputs_by_pid

    # Resolve each product's type from its taxonomy_v1 output (if any).
    for p in products:
        product_type = "unknown"
        for out in outputs_by_pid.get(p.product_id, []):
            if out.strategy == "taxonomy_v1":
                product_type = str(out.attributes.get("product_type") or "unknown")
                break
        type_to_pids[product_type].append(p.product_id)

    type_schemas: list[ProductTypeSchema] = []
    for ptype, pids in type_to_pids.items():
        slot_keys: dict[str, list[str]] = defaultdict(list)  # key -> source strategies
        for pid in pids:
            for out in pid_outputs_index.get(pid, []):
                for key in out.attributes.keys():
                    slot_keys[key].append(out.strategy)
                # Drill into parsed_specs / scraped_specs sub-dicts.
                for sub_key in ("parsed_specs", "scraped_specs"):
                    sub = out.attributes.get(sub_key)
                    if isinstance(sub, dict):
                        for k in sub.keys():
                            slot_keys[k].append(out.strategy)
        slots: list[SlotSchema] = []
        for key, strategies in sorted(slot_keys.items()):
            slots.append(
                SlotSchema(
                    key=key,
                    type=_infer_type(key),
                    fill_rate=1.0,  # rough — refined when we add value sampling
                    source_strategies=sorted(set(strategies)),
                )
            )
        type_schemas.append(
            ProductTypeSchema(
                product_type=ptype,
                sample_count=len(pids),
                common_slots=slots,
            )
        )

    return CatalogSchema(
        merchant_id=merchant_id,
        generated_at=datetime.now(timezone.utc),
        catalog_size=len(products),
        product_types=type_schemas,
    )


def _all_slots(schema: CatalogSchema) -> list[SlotSchema]:
    out: list[SlotSchema] = []
    seen: set[str] = set()
    for pt in schema.product_types:
        for slot in pt.common_slots:
            if slot.key not in seen:
                seen.add(slot.key)
                out.append(slot)
    return out


def _infer_type(key: str) -> str:
    if key.startswith("good_for_") or key.startswith("is_") or key.startswith("has_"):
        return "boolean"
    for suffix in ("_gb", "_mb", "_kg", "_lbs", "_hz", "_w", "_l", "_cm", "_mm", "_hours", "_count"):
        if key.endswith(suffix):
            return "numeric"
    if key in {"product_type_confidence"}:
        return "numeric"
    return "text"


# ---------------------------------------------------------------------------
# Helpers exported for the CLI
# ---------------------------------------------------------------------------


def serialize_summary(summary: RunSummary) -> str:
    import json

    return json.dumps(summary.to_dict(), ensure_ascii=False, indent=2)


def serialize_full(result: RunResult) -> str:
    import json

    return json.dumps(
        {
            "summary": result.summary.to_dict(),
            "assessment": result.assessment.model_dump(mode="json"),
            "catalog_schema": result.schema.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def write_assessment_artifact(assessment: AssessorOutput, path) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(serialize_assessment(assessment), encoding="utf-8")
