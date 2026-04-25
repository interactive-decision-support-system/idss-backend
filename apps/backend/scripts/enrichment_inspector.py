#!/usr/bin/env python3
"""Enrichment Inspector — lightweight Streamlit viz for the multi-agent
enrichment module.

Launch:
    streamlit run scripts/enrichment_inspector.py

Inputs:
    * runs/*.json emitted by scripts/run_enrichment.py --eval-output
        (summary + assessment + catalog_schema + per_product_results)
    * merchants.registry (#53) — populates the merchant selector and scopes
        the KG tier by (merchant_id, kg_strategy).
    * merchants.products_<m> + merchants.products_enriched_<m> — for the
        Enriched Table tab, joined by product_id.
    * merchant_agent.kg_projection — IDENTITY_FIELDS, FLATTENING_RULES, KEY_PATTERNS,
        cypher_referenced_properties() for the KG tier and coverage metric.

Env vars:
    * DATABASE_URL            — same connection the apps/backend uses.
    * MERCHANT_ADMIN_HOST     — optional. When set, the merchant selector
      hits ``GET /merchant`` on this host (once PR #66 lands) instead of
      reading merchants.registry directly. Same six fields either way.
    * LANGFUSE_HOST           — optional; used to deep-link the Run Summary
      tab into the Langfuse UI filtered by ``tags: run:<run_id>``.

The app is intentionally resilient: if the selected merchant is deleted
mid-session (DELETE /merchant/{id} — PR #66), the table queries catch the
missing-relation error, drop a refresh prompt, and re-hydrate the
registry row list.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# Make the apps/backend package importable so we can reuse kg_projection /
# registry statics without duplicating them here.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, ProgrammingError

from merchant_agent import kg_projection
from merchant_agent.enrichment import registry as enrichment_registry
from merchant_agent.merchant_agent import merchant_catalog_table, merchant_enriched_table


RUNS_DIR = _REPO_ROOT / "runs"
TRACES_DIR = _REPO_ROOT / "logs" / "enrichment_traces"
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
MERCHANT_ADMIN_HOST = os.getenv("MERCHANT_ADMIN_HOST", "").rstrip("/") or None


# ---------------------------------------------------------------------------
# DB + registry helpers
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    url = os.getenv("DATABASE_URL")
    if not url:
        st.error(
            "DATABASE_URL is not set. Copy .env.example → .env and fill in "
            "your Postgres connection string."
        )
        st.stop()
    return create_engine(url, pool_pre_ping=True)


def _fmt_ts(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else ""


def list_merchants_from_db(engine: Engine) -> list[dict[str, Any]]:
    """Read merchants.registry and enrich each row with a live catalog_size
    from COUNT(*) on the per-merchant raw table. Matches the six-field shape
    that GET /merchant (#66) returns."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT merchant_id, domain, strategy, kg_strategy, created_at "
                "FROM merchants.registry ORDER BY merchant_id"
            )
        ).mappings().all()
    merchants: list[dict[str, Any]] = []
    for r in rows:
        mid = r["merchant_id"]
        size: int | None
        try:
            raw = merchant_catalog_table(mid)
            with engine.connect() as conn:
                size = int(
                    conn.execute(text(f'SELECT COUNT(*) FROM {raw}')).scalar()
                    or 0
                )
        except (ProgrammingError, DatabaseError, ValueError):
            # ProgrammingError / DatabaseError: table missing
            # (DELETE /merchant mid-session). ValueError: registry holds a
            # malformed merchant_id (shouldn't happen but #71's verified
            # factory makes silent leakage worse than a None size).
            size = None
        merchants.append(
            {
                "merchant_id": mid,
                "domain": r["domain"],
                "strategy": r["strategy"],
                "kg_strategy": r["kg_strategy"],
                "catalog_size": size,
                "created_at": _fmt_ts(r["created_at"]),
            }
        )
    return merchants


def list_merchants_from_http(host: str) -> list[dict[str, Any]] | None:
    """GET /merchant on the admin host (PR #66). Returns None on any error
    so the caller can fall back to the DB path."""
    try:
        import urllib.request

        with urllib.request.urlopen(f"{host}/merchant", timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - HTTP is best-effort
        return None
    if not isinstance(payload, list):
        return None
    return payload


def list_merchants(engine: Engine) -> list[dict[str, Any]]:
    if MERCHANT_ADMIN_HOST is not None:
        http = list_merchants_from_http(MERCHANT_ADMIN_HOST)
        if http is not None:
            return http
        st.caption(
            f"_MERCHANT_ADMIN_HOST=`{MERCHANT_ADMIN_HOST}` unreachable "
            "— falling back to DB._"
        )
    return list_merchants_from_db(engine)


# ---------------------------------------------------------------------------
# Coverage breakdown (vision bullet 3 — per-cell source_kind provenance)
# ---------------------------------------------------------------------------

# Canonical source_kind values from PR #97's SourceKind enum, plus "unknown"
# for decisions written before that PR or with missing source_kind fields.
_CANONICAL_SOURCE_KINDS: tuple[str, ...] = (
    "raw_parse",
    "scrape",
    "parametric",
    "deterministic_fallback",
    "unknown",
)

# Path to feature-discovery coverage.json outputs (PR #91, optional).
_FEATURE_DISCOVERY_DIR = _REPO_ROOT / "runs" / "feature_discovery"


@dataclass
class CoverageBreakdown:
    """Per-merchant cell-count breakdown by source_kind.

    Built by ``compute_coverage_breakdown`` from
    ``merchants.products_enriched_<merchant_id>`` rows with
    ``strategy='composer_v1'``.

    Fields
    ------
    total_products : int
        Number of rows queried (may be 0 for a fresh / failed merchant).
    total_cells : int
        Sum of ``len(composed_fields)`` across all products.
    by_source_kind : dict[str, int]
        Cell count per source_kind bucket.  Keys come from
        ``_CANONICAL_SOURCE_KINDS`` plus any other value present in the data.
    by_attribute : dict[str, dict[str, int]]
        ``{attr_name: {source_kind: count}}`` across all products.
    missing_per_attribute : dict[str, int]
        Count of products where the attribute is absent from
        ``composed_fields``.
    incomplete_decisions_count : int
        Number of products whose ``attributes['incomplete_decisions']`` is True
        (set by the PR #97 reconciler when it had to synthesise decisions).
    """

    total_products: int = 0
    total_cells: int = 0
    by_source_kind: dict[str, int] = field(default_factory=dict)
    by_attribute: dict[str, dict[str, int]] = field(default_factory=dict)
    missing_per_attribute: dict[str, int] = field(default_factory=dict)
    incomplete_decisions_count: int = 0


def compute_coverage_breakdown(engine: Engine, merchant_id: str) -> CoverageBreakdown:
    """Query ``merchants.products_enriched_<merchant_id>`` (strategy=
    ``composer_v1``) and return a ``CoverageBreakdown``.

    Pure function — no Streamlit calls.  Safe to call from tests with a
    mocked/in-memory engine.

    Edge cases handled:
    * No rows → all-zero breakdown.
    * Row with ``composed_fields={}`` (current zero-signal state on
      mocklaptops) → counts toward total_products but adds 0 cells.
    * Decision missing ``source_kind`` (pre-PR-#97 traces) → bucketed as
      ``"unknown"``.
    * Composed key with no matching decision → counted under the source_kind
      of the synthesised sentinel (``"parametric"`` per the reconciler) but
      we default to ``"unknown"`` for maximum honesty.
    * ValueError from ``merchant_enriched_table`` (bad slug) → empty breakdown.
    """
    try:
        enr = merchant_enriched_table(merchant_id)
    except ValueError:
        return CoverageBreakdown()

    sql = text(
        f"SELECT attributes FROM {enr} WHERE strategy = 'composer_v1'"
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
    except (ProgrammingError, DatabaseError):
        return CoverageBreakdown()

    bd = CoverageBreakdown()
    # First pass: collect composed_fields data to build the attribute universe.
    parsed_rows: list[dict[str, Any]] = []
    attr_universe: set[str] = set()

    for r in rows:
        attrs: dict[str, Any] = r["attributes"] or {}
        if not isinstance(attrs, dict):
            continue
        composed: dict[str, Any] = attrs.get("composed_fields") or {}
        if not isinstance(composed, dict):
            composed = {}
        decisions: list[Any] = attrs.get("composer_decisions") or []
        if not isinstance(decisions, list):
            decisions = []
        incomplete: bool = bool(attrs.get("incomplete_decisions", False))

        parsed_rows.append(
            {
                "composed": composed,
                "decisions": decisions,
                "incomplete": incomplete,
            }
        )
        attr_universe.update(composed.keys())

    # Ensure by_attribute and missing_per_attribute cover the full universe.
    for attr in attr_universe:
        bd.by_attribute.setdefault(attr, {})
        bd.missing_per_attribute.setdefault(attr, 0)

    bd.total_products = len(parsed_rows)

    for pr in parsed_rows:
        composed = pr["composed"]
        decisions: list[Any] = pr["decisions"]
        incomplete: bool = pr["incomplete"]

        if incomplete:
            bd.incomplete_decisions_count += 1

        # Build a key → source_kind lookup from this product's decisions.
        decision_by_key: dict[str, str] = {}
        for d in decisions:
            if not isinstance(d, dict):
                continue
            k = d.get("key")
            if not isinstance(k, str) or not k:
                continue
            sk = d.get("source_kind")
            if sk is None:
                sk = "unknown"
            elif isinstance(sk, str) and sk:
                pass  # use as-is
            else:
                sk = "unknown"
            decision_by_key[k] = sk

        # Tally cells and missing counts against the full attribute universe.
        for attr in attr_universe:
            if attr in composed:
                bd.total_cells += 1
                sk = decision_by_key.get(attr, "unknown")
                bd.by_source_kind[sk] = bd.by_source_kind.get(sk, 0) + 1
                bd.by_attribute[attr][sk] = bd.by_attribute[attr].get(sk, 0) + 1
            else:
                bd.missing_per_attribute[attr] = (
                    bd.missing_per_attribute.get(attr, 0) + 1
                )

    return bd


def _latest_feature_discovery_coverage(product_type: str | None = None) -> dict[str, Any] | None:
    """Return the most-recently-modified coverage.json from
    ``runs/feature_discovery/<product_type>/<latest>/coverage.json``.

    Returns ``None`` silently if PR #91 hasn't landed yet (directory absent)
    or no files exist.  ``product_type=None`` scans all subdirectories."""
    if not _FEATURE_DISCOVERY_DIR.exists():
        return None
    try:
        candidates: list[Path] = []
        if product_type:
            base = _FEATURE_DISCOVERY_DIR / product_type
            candidates = list(base.rglob("coverage.json")) if base.is_dir() else []
        else:
            candidates = list(_FEATURE_DISCOVERY_DIR.rglob("coverage.json"))
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        with open(latest, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Run artifact helpers
# ---------------------------------------------------------------------------


def list_run_artifacts() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        RUNS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


@st.cache_data(show_spinner=False)
def load_run(path_str: str) -> dict[str, Any]:
    with open(path_str, "r", encoding="utf-8") as fh:
        return json.load(fh)


@st.cache_data(show_spinner=False)
def _artifact_merchant_id(path_str: str, mtime: float) -> str | None:
    """Return ``summary.merchant_id`` for ``path_str``.

    First call for a given ``(path, mtime)`` pair does a full
    ``json.load`` of the artifact — there's no streaming shortcut. The
    cache then ensures every subsequent Streamlit rerun reads from
    memory until the file is overwritten (``mtime`` changes). For
    steady-state navigation this is effectively free; it's the *cold
    load* across ``runs/`` that costs — keep artifacts reasonably small
    and this stays under tens of milliseconds per file.

    The ``mtime`` argument is the cache-bust lever and is unused in the
    body — Streamlit's ``@cache_data`` hashes every argument for the
    key, so passing the file's mtime means an in-place overwrite
    invalidates the entry without the caller having to reason about
    it. Review feedback #2, PR #86."""
    del mtime  # documented cache key, not consumed here
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            summary = (json.load(fh).get("summary") or {})
    except (OSError, json.JSONDecodeError):
        return None
    mid = summary.get("merchant_id")
    return str(mid) if mid is not None else None


def list_run_artifacts_for_merchant(merchant_id: str) -> list[Path]:
    """Filter ``list_run_artifacts()`` to artifacts whose
    ``summary.merchant_id`` matches. Reads each artifact's summary via a
    per-file mtime-keyed cache (`_artifact_merchant_id`) so the typical
    "same files, user flipped a selector" rerun does zero disk I/O."""
    out: list[Path] = []
    for p in list_run_artifacts():
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if _artifact_merchant_id(str(p), mtime) == merchant_id:
            out.append(p)
    return out


def langfuse_tag_url(run_id: str) -> str:
    q = urllib.parse.urlencode({"tags": f"run:{run_id}"})
    return f"{LANGFUSE_HOST}/traces?{q}"


# ---------------------------------------------------------------------------
# Catalog queries (raw + enriched join)
# ---------------------------------------------------------------------------


def fetch_enriched_table(
    engine: Engine, merchant_id: str, limit: int = 500
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]] | None:
    """Join raw products and enriched attributes for one merchant. The
    composer_v1 row is flattened into ``canonical.<key>`` columns (one per
    entry in its ``composed_fields``); every other strategy's row is
    pivoted into ``<strategy>.<key>`` columns as pre-composer findings
    (debug view). Returns ``(rows, raw_columns, canonical_columns,
    finding_columns)`` or ``None`` if the merchant's tables are gone
    (DELETE /merchant mid-session) **or** the slug doesn't match
    ``MERCHANT_ID_RE`` (stale URL param, malformed test fixture, any
    future path that bypasses the registry-backed selector).

    Splitting canonical vs findings is the read-time counterpart of
    composer_v1 being the single writer of the catalog row (issue #83).
    Canonical columns drive the #81 cell-lineage UX — each canonical cell
    has a composer_decisions entry that names the producing agent; finding
    cells are what the composer chose from."""
    # Route through the canonical helpers so a future schema-per-tenant
    # move (issue #38) only has to touch ``merchant_*_table``. The helpers
    # also re-validate the slug against MERCHANT_ID_RE — interpolation
    # below is safe by construction.
    try:
        raw = merchant_catalog_table(merchant_id)
        enr = merchant_enriched_table(merchant_id)
    except ValueError:
        return None
    # The raw table's PK column is ``id`` and the title column is ``title``;
    # the ORM model exposes them as ``product_id`` / ``name``. Hand-written
    # SQL has to use the underlying column names. Enriched rows live in a
    # per-merchant table by construction, and the enriched schema doesn't
    # carry ``merchant_id`` at all, so the JOIN keys on ``id``/``product_id``
    # only.
    sql = text(
        f"""
        SELECT p.id AS product_id, p.title AS name, p.brand, p.category,
               p.price, p.attributes AS raw_attributes,
               e.strategy, e.attributes AS enriched_attributes
        FROM {raw} p
        LEFT JOIN {enr} e ON p.id = e.product_id
        ORDER BY p.id
        LIMIT :lim
        """
    )
    try:
        with engine.connect() as conn:
            raw_rows = conn.execute(sql, {"lim": limit}).mappings().all()
    except (ProgrammingError, DatabaseError):
        return None

    by_pid: dict[Any, dict[str, Any]] = {}
    raw_cols = ["product_id", "name", "brand", "category", "price"]
    canonical_cols: set[str] = set()
    finding_cols: set[str] = set()
    for r in raw_rows:
        pid = r["product_id"]
        if pid not in by_pid:
            by_pid[pid] = {
                "product_id": pid,
                "name": r["name"],
                "brand": r["brand"],
                "category": r["category"],
                "price": r["price"],
            }
            # Surface a handful of common raw JSONB keys as their own columns
            # so the row mirrors what the agent actually saw.
            ra = r["raw_attributes"] or {}
            for k in sorted(ra.keys()):
                col = f"raw.{k}"
                if col not in raw_cols:
                    raw_cols.append(col)
                # Avoid serializing complex dicts to cells.
                v = ra[k]
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False, default=str)[:160]
                by_pid[pid][col] = v
        strategy = r["strategy"]
        attrs = r["enriched_attributes"] or {}
        if not (strategy and isinstance(attrs, dict)):
            continue
        if strategy == "composer_v1":
            # The composer's attributes are {composed_fields, composer_decisions,
            # composed_at}. Flatten composed_fields into canonical.<key> columns;
            # keep composer_decisions + composed_at out of the grid (they feed
            # the side panel via the JSONL trace instead).
            composed = attrs.get("composed_fields") or {}
            if isinstance(composed, dict):
                for k, v in composed.items():
                    col = f"canonical.{k}"
                    canonical_cols.add(col)
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, ensure_ascii=False, default=str)[:160]
                    by_pid[pid][col] = v
            continue
        for k, v in attrs.items():
            col = f"{strategy}.{k}"
            finding_cols.add(col)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, default=str)[:160]
            by_pid[pid][col] = v

    rows = list(by_pid.values())
    return rows, raw_cols, sorted(canonical_cols), sorted(finding_cols)


def fetch_one_product(
    engine: Engine, merchant_id: str, product_id: str
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """Return (raw_row_dict, {strategy: attributes_dict}) for the drill-down.

    Returns ``(None, {})`` when ``merchant_id`` doesn't match
    ``MERCHANT_ID_RE`` — same defensive posture as ``fetch_enriched_table``,
    so a stale URL param can't surface as an uncaught ``ValueError``."""
    try:
        raw = merchant_catalog_table(merchant_id)
        enr = merchant_enriched_table(merchant_id)
    except ValueError:
        return None, {}
    raw_row: dict[str, Any] | None
    with engine.connect() as conn:
        # Raw table PK is ``id``; the ORM exposes it as ``product_id``. Use
        # the underlying name in SQL, then translate the dict keys for the
        # display + kg_projection.IDENTITY_FIELDS lookup downstream.
        rr = conn.execute(
            text(f"SELECT * FROM {raw} WHERE id = :pid LIMIT 1"),
            {"pid": product_id},
        ).mappings().one_or_none()
        raw_row = dict(rr) if rr else None
        # Enriched table PK is literally ``product_id``; leave as-is.
        enriched_rows = conn.execute(
            text(
                f"SELECT strategy, attributes FROM {enr} "
                f"WHERE product_id = :pid"
            ),
            {"pid": product_id},
        ).mappings().all()
    if raw_row is not None:
        # Add ORM-attribute / kg_projection logical names alongside the raw
        # column names so render_per_product's IDENTITY_FIELDS lookup hits.
        # We copy rather than rename (``raw_row[logical] = raw_row[col]``)
        # so the Raw panel downstream still shows the original catalog keys
        # (``id`` / ``title`` / ``imageurl``) the merchant uploaded — that's
        # the whole point of the "raw" view.
        for col, logical in (("id", "product_id"), ("title", "name"), ("imageurl", "image_url")):
            if col in raw_row and logical not in raw_row:
                raw_row[logical] = raw_row[col]
    enriched_by_strategy: dict[str, dict[str, Any]] = {
        r["strategy"]: (r["attributes"] or {}) for r in enriched_rows
    }
    return raw_row, enriched_by_strategy


# ---------------------------------------------------------------------------
# KG tier statics
# ---------------------------------------------------------------------------


def kg_property_catalog() -> list[dict[str, Any]]:
    """Build the KG-side reference table: every property the :Product node
    can carry, annotated with producer + whether the Cypher reader uses it.

    The ``notes`` column surfaces calibration tensions the projection itself
    can't express — currently the soft-tagger threshold (#60), which gates
    every ``good_for_*`` flag at ``coalesce(p.flag, 0.0) >= TAG_CONFIDENCE_THRESHOLD``
    in the Cypher scorer. Floats are stored as-is so the threshold can move
    without a rebuild; the dashboard surfaces the *current* value so a
    reader looking at a low-confidence soft tag can tell why it's not
    contributing to ``soft_score``.
    """
    try:
        referenced = kg_projection.cypher_referenced_properties()
    except Exception:
        referenced = set()
    try:
        tag_threshold = float(kg_projection.TAG_CONFIDENCE_THRESHOLD)
    except (AttributeError, TypeError, ValueError):
        # Mirrors the projection default; narrow tuple keeps surprises (e.g.
        # an ImportError during a partial refactor) loud instead of silent.
        tag_threshold = 0.5
    soft_tag_note = (
        f"stored as float; thresholded ≥ {tag_threshold} in Cypher (#60)"
    )

    rows: list[dict[str, Any]] = []
    for k in sorted(kg_projection.IDENTITY_FIELDS):
        rows.append(
            {
                "property": k,
                "producer": "identity",
                "reader_references": k in referenced,
                "notes": "",
            }
        )
    for strategy, rule in kg_projection.FLATTENING_RULES.items():
        rows.append(
            {
                "property": f"<rule:{strategy}>",
                "producer": strategy,
                "reader_references": "(dynamic)",
                "notes": "",
            }
        )
    for pat in kg_projection.KEY_PATTERNS:
        # Open-vocab good_for_* tags are the only KEY_PATTERNS today, but
        # gate the note on the actual strategy so a future soft-tagger
        # variant — or a different open-vocab pattern — doesn't inherit it
        # by accident.
        notes = soft_tag_note if pat.strategy == "soft_tagger_v1" else ""
        rows.append(
            {
                "property": pat.regex.pattern,
                "producer": f"pattern:{pat.strategy}",
                "reader_references": "(pattern)",
                "notes": notes,
            }
        )
    for k in sorted(kg_projection.RESERVED_BOOL_FEATURES):
        rows.append(
            {
                "property": k,
                "producer": "reserved (#61)",
                "reader_references": k in referenced,
                "notes": "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def render_run_summary(run: dict[str, Any]) -> None:
    summary = run.get("summary", {})
    st.subheader("Run summary")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Products", summary.get("products_processed", 0))
    col2.metric(
        "Avg keys/product",
        f"{summary.get('avg_keys_filled_per_product', 0):.2f}",
    )
    col3.metric("Total cost (USD)", f"${summary.get('total_cost_usd', 0):.4f}")
    col4.metric("Total latency (ms)", summary.get("total_latency_ms", 0))

    meta_cols = st.columns(3)
    meta_cols[0].markdown(f"**run_id**: `{summary.get('run_id', '?')}`")
    meta_cols[1].markdown(f"**merchant_id**: `{summary.get('merchant_id', '?')}`")
    meta_cols[2].markdown(f"**kg_strategy**: `{summary.get('kg_strategy', '?')}`")
    run_id = summary.get("run_id")
    if run_id:
        st.markdown(
            f"[Open this run in Langfuse →]({langfuse_tag_url(run_id)})  "
            f"_(filters by `tags: run:{run_id}`)_"
        )

    st.markdown("**Strategies invoked / succeeded / failed**")
    strategies = sorted(
        set(summary.get("strategies_invoked", {}).keys())
        | set(summary.get("strategies_succeeded", {}).keys())
        | set(summary.get("strategies_failed", {}).keys())
    )
    st.dataframe(
        [
            {
                "strategy": s,
                "invoked": summary.get("strategies_invoked", {}).get(s, 0),
                "succeeded": summary.get("strategies_succeeded", {}).get(s, 0),
                "failed": summary.get("strategies_failed", {}).get(s, 0),
            }
            for s in strategies
        ],
        hide_index=True,
        use_container_width=True,
    )

    cov = summary.get("kg_reader_coverage")
    if cov:
        st.markdown("**KG reader coverage (this run)**")
        if cov.get("kg_built") is False:
            st.info(
                "KG not built yet for "
                f"`({cov.get('merchant_id')}, {cov.get('kg_strategy')})`. "
                "Tracked in #39 — search will hit the positional fallback."
            )
        else:
            cov_cols = st.columns(4)
            cov_cols[0].metric("Reader refs", cov.get("referenced", 0))
            cov_cols[1].metric("Producible", cov.get("producible", 0))
            cov_cols[2].metric("Produced this run", cov.get("produced_this_run", 0))
            cov_cols[3].metric("Missing", len(cov.get("missing", [])))


def render_raw_enriched_kg(
    run: dict[str, Any], engine: Engine, merchant_id: str
) -> None:
    assessment = run.get("assessment", {})
    catalog_schema = run.get("catalog_schema", {})
    summary = run.get("summary", {})

    col_r, col_e, col_k = st.columns(3)

    with col_r:
        st.markdown("### Raw columns the agent saw")
        density = assessment.get("column_density", {})
        st.caption(f"catalog_size = **{assessment.get('catalog_size', '?')}**")
        if density:
            st.dataframe(
                [
                    {"column": k, "density": v}
                    for k, v in sorted(density.items(), key=lambda kv: -kv[1])
                ],
                hide_index=True,
                use_container_width=True,
            )
        sparse = assessment.get("sparse_attribute_keys", [])
        if sparse:
            known = set(enrichment_registry.KNOWN_RAW_ATTRIBUTE_KEYS)
            in_registry = [k for k in sparse if k in known]
            unexpected = [k for k in sparse if k not in known]
            st.markdown(f"**JSONB keys (registry vocab):** {len(in_registry)}")
            st.code(", ".join(sorted(in_registry)) or "(none)")
            if unexpected:
                st.markdown(
                    f"**Unexpected JSONB keys:** {len(unexpected)}"
                )
                st.code(", ".join(sorted(unexpected)))

    with col_e:
        st.markdown("### Enriched slots produced")
        types = catalog_schema.get("product_types", [])
        if not types:
            st.info("No enriched slots yet — run enrichment first.")
        for ptype in types:
            st.markdown(
                f"**{ptype.get('product_type', '?')}** "
                f"({ptype.get('sample_count', 0)} products)"
            )
            slots = ptype.get("common_slots", [])
            if slots:
                st.dataframe(
                    [
                        {
                            "slot": s.get("key"),
                            "type": s.get("type"),
                            "fill_rate": s.get("fill_rate"),
                            "sources": ",".join(s.get("source_strategies", [])),
                        }
                        for s in slots
                    ],
                    hide_index=True,
                    use_container_width=True,
                )

    with col_k:
        st.markdown("### KG node properties")
        st.caption(
            f"scoped by (merchant_id=`{merchant_id}`, "
            f"kg_strategy=`{summary.get('kg_strategy', '?')}`)"
        )
        st.dataframe(kg_property_catalog(), hide_index=True, use_container_width=True)

    # Header strip.
    n_cols = len(assessment.get("column_density", {}))
    n_json = len(assessment.get("sparse_attribute_keys", []))
    n_slots = sum(
        len(pt.get("common_slots", []))
        for pt in catalog_schema.get("product_types", [])
    )
    n_strategies = len(summary.get("strategies_succeeded", {}))
    cov = summary.get("kg_reader_coverage") or {}
    n_ref = cov.get("referenced", 0)
    n_missing = len(cov.get("missing", []))
    st.info(
        f"Saw **{n_cols}** raw columns / **{n_json}** JSONB keys → produced "
        f"**{n_slots}** enriched slots across **{n_strategies}** strategies → "
        f"**{n_ref}** reader-referenced KG properties "
        f"(**{n_missing}** unproduced)"
    )


# ---------------------------------------------------------------------------
# Cell-lineage side panel (issue #81) — reads composer_v1's composer_decisions
# audit log from the JSONL trace and renders the producing agent's full span
# (input, output, nested LLM prompt/response) on click.
# ---------------------------------------------------------------------------


# Upstream strategies composer_v1 may cite as source_strategy. Pulled
# from composer._SHORT_TO_STRATEGY at import time so the inspector
# never drifts from the writer's known-source set (review fix #5,
# PR #86). The hardcoded fallback matches that set as of PR #84's head
# and is only used if the import chain is broken (e.g. stripped-down
# test env where the app package isn't on sys.path).
_FALLBACK_UPSTREAM_STRATEGIES: tuple[str, ...] = (
    "taxonomy_v1",
    "parser_v1",
    "specialist_v1",
    "scraper_v1",
    "soft_tagger_v1",
)
try:
    from merchant_agent.enrichment.agents.composer import (
        _SHORT_TO_STRATEGY as _COMPOSER_SHORT_TO_STRATEGY,
    )

    _KNOWN_UPSTREAM_STRATEGIES: tuple[str, ...] = tuple(
        sorted(_COMPOSER_SHORT_TO_STRATEGY.values())
    )
except ImportError:
    # Only swallow missing-module cases — things like a stripped-down test
    # env where ``app`` isn't on sys.path. Other exceptions (e.g. env-var
    # assertions in a transitive import, AttributeError from a renamed
    # symbol) should surface so they're seen and fixed instead of
    # silently degrading to the hardcoded fallback. Review feedback #4,
    # PR #86.
    _KNOWN_UPSTREAM_STRATEGIES = _FALLBACK_UPSTREAM_STRATEGIES


@st.cache_data(show_spinner=False)
def _index_trace_by_product(
    jsonl_path_str: str, _mtime: float
) -> dict[str, dict[str, Any]]:
    """Parse the run's JSONL once and bucket spans by product_id.

    The ``_mtime`` argument is the cache-bust lever: Streamlit's
    ``@st.cache_data`` keys on every argument, so passing the file's
    modification time means a re-run overwrites the cache on file
    change without the caller having to reason about it. We also trust
    that ``run_id`` is globally unique — it is a 32-char hex UUID
    generated in ``orchestration/runner.py`` — so caching on the file
    path alone (no explicit run_id hash) is safe across merchants.

    Returns ``{pid: {"strategy_spans": {strategy: span}, "llm_by_strategy":
    {strategy: [span, ...]}, "composer": span | None, "decisions_by_key":
    {canonical_key: decision}}}``. Missing pids return an empty shell
    lazily, so callers don't need ``KeyError`` guards.

    LLM spans don't usually carry a product_id on their own input and
    the tracer doesn't auto-tag them with ``product:<pid>`` (see
    ``tracing._current_tags``), so attribution is done by time-window
    containment: an LLM span at ``[start, end]`` belongs to the
    strategy span whose ``[start, end]`` fully contains it. This is
    robust against JSONL write order (LLM spans close and flush
    *before* their parent strategy span, so "most recently seen
    strategy name" would always mis-attribute) and against any future
    interleaving — review fixes #2 and #3, PR #86.

    When an LLM span *does* carry a pid (via ``input.product_id`` or a
    ``product:<pid>`` tag — both already handled by
    ``_span_product_id``), we additionally require the enclosing
    strategy to share that pid. Today the pipeline runs products
    serially per worker so this is redundant, but the moment
    enrichment parallelises across products (``asyncio.gather`` or
    similar) two products' windows can overlap and the pure
    time-window match would mis-attribute an LLM call to the other
    product's strategy. Pid-constrained match prevents that without
    breaking the current pid-less case — review feedback #1, PR #86.

    Note on memory: the per-strategy LLM lists are unbounded. For a
    10k-product run with many LLM retries this bloats Streamlit's
    cache_data entry. Not a v1 blocker (the expected working set is
    ~dozens of products per inspection) — review feedback #6, PR #86.
    """
    path = Path(jsonl_path_str)
    if not path.exists():
        return {}
    strategy_records: list[dict[str, Any]] = []
    llm_records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = span.get("name") or ""
            if name.startswith("llm:"):
                llm_records.append(span)
                continue
            pid = _span_product_id(span)
            if not pid:
                continue
            strategy_records.append({"pid": str(pid), "name": name, "span": span})

    by_pid: dict[str, dict[str, Any]] = {}
    for rec in strategy_records:
        pid = rec["pid"]
        name = rec["name"]
        span = rec["span"]
        bucket = by_pid.setdefault(
            pid,
            {
                "strategy_spans": {},
                "llm_by_strategy": {},
                "composer": None,
                "decisions_by_key": {},
            },
        )
        if name == "composer_v1":
            bucket["composer"] = span
            for decision in _composer_decisions_from_span(span):
                key = decision.get("key")
                if isinstance(key, str) and key:
                    bucket["decisions_by_key"][key] = decision
        else:
            # Known upstream + any unknown span with a product_id both
            # land here so the agent graph can render diagnostic spans
            # if someone adds them. Namespace-routing on the UI side
            # only dispatches to the known set.
            bucket["strategy_spans"][name] = span

    # Time-window attribution for LLM spans. Build a flat list of
    # (start, end, pid, owner_name) covering every strategy + composer
    # span; for each LLM span, pick the tightest (smallest duration)
    # enclosing strategy as its owner. Ties (shouldn't happen in
    # practice — strategies for a single product run serially) are
    # broken by insertion order, which matches JSONL write order.
    intervals: list[tuple[float, float, str, str]] = []
    for rec in strategy_records:
        span = rec["span"]
        start = _span_started_at(span)
        end = _span_ended_at(span)
        if start is None or end is None:
            continue
        intervals.append((start, end, rec["pid"], rec["name"]))
    # composer_v1 is already captured in strategy_records above (it has
    # a product_id on its input), so it's an attribution target too —
    # that's how the composer's LLM call gets bucketed under composer_v1.
    for llm in llm_records:
        lstart = _span_started_at(llm)
        lend = _span_ended_at(llm)
        if lstart is None or lend is None:
            continue
        llm_pid = _span_product_id(llm)  # may be None (pid-less LLM span)
        owner: tuple[str, str] | None = None
        best_duration = float("inf")
        for (s_start, s_end, s_pid, s_name) in intervals:
            # If the LLM span carries a pid, require the enclosing
            # strategy to share it — future-proof against concurrent
            # products whose windows overlap. Falls through to pure
            # time-window match when the LLM span is pid-less, which
            # is the common case today.
            if llm_pid is not None and s_pid != llm_pid:
                continue
            if s_start <= lstart and lend <= s_end:
                duration = s_end - s_start
                if duration < best_duration:
                    owner = (s_pid, s_name)
                    best_duration = duration
        if owner is None:
            continue
        pid, owner_name = owner
        bucket = by_pid.setdefault(
            pid,
            {
                "strategy_spans": {},
                "llm_by_strategy": {},
                "composer": None,
                "decisions_by_key": {},
            },
        )
        bucket["llm_by_strategy"].setdefault(owner_name, []).append(llm)
    return by_pid


def _span_started_at(span: dict[str, Any]) -> float | None:
    val = span.get("started_at")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _span_ended_at(span: dict[str, Any]) -> float | None:
    val = span.get("ended_at")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _composer_decisions_from_span(span: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the decisions list out of a composer_v1 span.

    The runner writes ``output`` inside ``updates[*].output`` — ``_span_output``
    handles that walk. The composer's output is a StrategyOutput dump, so
    decisions live at ``output.attributes.composer_decisions``."""
    out = _span_output(span) or {}
    if not isinstance(out, dict):
        return []
    attrs = out.get("attributes") or {}
    decisions = attrs.get("composer_decisions") or []
    if not isinstance(decisions, list):
        return []
    return [d for d in decisions if isinstance(d, dict)]


def _composer_notes(span: dict[str, Any] | None) -> str | None:
    """Return composer_v1's StrategyOutput.notes (e.g. 'deterministic_fallback',
    'no_upstream_findings') or None when no composer span is recorded.

    ``notes`` is compared by ``is None`` rather than truthiness so an
    intentionally-empty marker string round-trips verbatim — downstream
    callers still filter empty via ``if not notes:`` but new markers
    don't get silently erased here (review nit #2, PR #86)."""
    if not span:
        return None
    out = _span_output(span) or {}
    if not isinstance(out, dict):
        return None
    notes = out.get("notes")
    if notes is None:
        return None
    return str(notes)


def _agent_graph_dot(
    strategy_spans: dict[str, dict[str, Any]],
    composer_span: dict[str, Any] | None,
    *,
    highlight_strategy: str | None,
    dashed_strategies: tuple[str, ...] = (),
    highlighted_key: str | None = None,
) -> str:
    """Emit a graphviz dot string for the per-product agent graph.

    Nodes: only strategies that contributed to *this* cell's lineage —
    the ``source_strategy`` (solid, highlighted) plus any strategy whose
    finding ended up in ``dropped_alternatives`` (dashed edge). The full
    "all agents observed" view is covered by the Contributing-agents
    expanders below the graph; this chart answers the narrower question
    "who produced this cell and who was in the running?".

    Edges always terminate at the ``composer_v1`` sink. Solid edges
    carry the canonical key as their label; dashed edges are marked
    ``dropped``.

    Sizing is clamped (``size="4,2.5"``) so the rendered DAG stays
    legible inside the side panel even on wide monitors — the caller
    must NOT pass ``use_container_width=True`` to ``st.graphviz_chart``
    or the clamp is overridden.
    """
    def _esc(s: str) -> str:
        # Minimal DOT escape — canonical keys and strategy names today
        # are safe identifiers, but a decision key containing a quote
        # or backslash would otherwise break the DOT body. Cheap
        # defense-in-depth since we don't have python-graphviz as a
        # dep (review nit #1, PR #86).
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines: list[str] = [
        "digraph G {",
        'rankdir=LR;',
        'size="4,2.5";',
        "nodesep=0.25;",
        "ranksep=0.45;",
        "node [shape=box, fontsize=10, margin=\"0.08,0.04\"];",
        "edge [fontsize=9];",
    ]
    edge_label = _esc(highlighted_key or "")
    contributors: list[str] = []
    if highlight_strategy and highlight_strategy in strategy_spans:
        contributors.append(highlight_strategy)
    for s in dashed_strategies:
        if s in strategy_spans and s != highlight_strategy and s not in contributors:
            contributors.append(s)
    for name in contributors:
        attrs = []
        if name == highlight_strategy:
            attrs.append("style=filled")
        attr_str = f" [{', '.join(attrs)}]" if attrs else ""
        lines.append(f'"{_esc(name)}"{attr_str};')
    if composer_span is not None:
        lines.append('"composer_v1" [shape=doublecircle];')
        if highlight_strategy and highlight_strategy in strategy_spans:
            lines.append(
                f'"{_esc(highlight_strategy)}" -> "composer_v1"'
                f' [label="{edge_label}"];'
            )
        for s in dashed_strategies:
            if s in strategy_spans and s != highlight_strategy:
                lines.append(
                    f'"{_esc(s)}" -> "composer_v1"'
                    ' [style=dashed, label="dropped"];'
                )
    if not contributors and composer_span is not None:
        lines.append('"(no contributors)" [shape=plaintext, fontcolor=gray50];')
        lines.append('"(no contributors)" -> "composer_v1" [style=dotted, color=gray50];')
    lines.append("}")
    return "\n".join(lines)


def _render_agent_node(
    strategy: str,
    span: dict[str, Any],
    llm_spans: list[dict[str, Any]],
    *,
    expanded: bool,
) -> None:
    """One collapsible per-agent node: the agent's span input/output, then
    its LLM child spans (prompt/response). Matches the shape of the
    global Reasoning-trace tab so the visual vocabulary is consistent."""
    dur = _span_duration_ms(span)
    md = _span_metadata(span)
    header_bits = [f"`{strategy}`"]
    if dur is not None:
        header_bits.append(f"{dur} ms")
    cost = md.get("cost_usd")
    if isinstance(cost, (int, float)):
        header_bits.append(f"${cost:.6f}")
    with st.expander("  ·  ".join(header_bits), expanded=expanded):
        inp_col, out_col = st.columns(2)
        with inp_col:
            st.markdown("**input**")
            st.json(_jsonable(span.get("input") or {}))
        with out_col:
            st.markdown("**output**")
            st.json(_jsonable(_span_output(span) or {}))
        model = md.get("model")
        if model:
            st.caption(f"model: `{model}`")
        if llm_spans:
            st.markdown(f"**LLM calls within this agent** ({len(llm_spans)})")
            for llm in llm_spans:
                lname = llm.get("name", "llm:?")
                ldur = _span_duration_ms(llm)
                lmd = _span_metadata(llm)
                label_bits = [lname]
                if ldur is not None:
                    label_bits.append(f"{ldur} ms")
                lcost = lmd.get("cost_usd")
                if isinstance(lcost, (int, float)):
                    label_bits.append(f"${lcost:.6f}")
                with st.expander("  ·  ".join(label_bits), expanded=False):
                    li, lo = st.columns(2)
                    with li:
                        st.markdown("**prompt**")
                        st.json(_jsonable(llm.get("input") or {}))
                    with lo:
                        st.markdown("**response**")
                        st.json(_jsonable(_span_output(llm) or {}))
                    tok_in = lmd.get("input_tokens", "?")
                    tok_out = lmd.get("output_tokens", "?")
                    st.caption(f"tokens: {tok_in} in / {tok_out} out")


def _render_composer_notes_callout(notes: str | None) -> None:
    """Surface the composer's StrategyOutput.notes as a small callout so
    a user inspecting a fallback row doesn't mistake it for LLM-composed."""
    if not notes:
        return
    if notes == "deterministic_fallback":
        st.warning(
            "Composer LLM failed for this product — canonical row built "
            "deterministically from findings (`notes='deterministic_fallback'`)."
        )
    elif notes == "no_upstream_findings":
        st.info(
            "No upstream strategies produced findings for this product; "
            "composer was invoked but had no material to compose."
        )


def _render_cell_panel_empty() -> None:
    st.markdown("### Cell lineage")
    st.caption(
        "Click a cell in the enriched table to trace who produced it. "
        "Canonical columns (`canonical.*`, green) show the full composer "
        "decision + contributing agent; finding columns (grey, debug "
        "view) show just the emitting agent's span."
    )


def _render_cell_panel_raw(col: str) -> None:
    st.markdown("### Cell lineage")
    st.caption(f"`{col}` is a raw catalog cell — no agent produced it.")


def _short_pid(pid: str) -> str:
    """Compact product_id for captions: show the 8-char prefix + ellipsis
    for UUID-length strings, otherwise show the id verbatim so numeric /
    short merchant-defined ids (e.g. ``sku-12``) don't render as
    ``sku-12…`` cosmetic nonsense (review fix #8, PR #86)."""
    s = str(pid)
    if len(s) <= 12:
        return s
    return f"{s[:8]}…"


def _render_cell_panel_canonical(
    bucket: dict[str, Any],
    pid: str,
    canonical_key: str,
) -> None:
    st.markdown("### Cell lineage")
    st.caption(f"`canonical.{canonical_key}`  ·  product `{_short_pid(pid)}`")
    if bucket.get("_pid_not_in_run"):
        st.warning(
            "This product isn't in the selected enrichment run — it was "
            "likely added to the catalog after the run finished. Pick a "
            "newer run from the sidebar, or re-run enrichment to include "
            "it."
        )
        return
    composer_span = bucket.get("composer")
    if composer_span is None:
        st.warning(
            "Composer didn't produce a span for this product in the "
            "selected run. Either the composer was disabled / errored "
            "for this specific product, or `ENRICHMENT_TRACE_JSONL=1` "
            "was off when the run executed."
        )
        return
    _render_composer_notes_callout(_composer_notes(composer_span))
    decision = bucket.get("decisions_by_key", {}).get(canonical_key)
    if decision is None:
        st.info(
            "The composer shipped this value but didn't record which "
            "agent produced it. This is a known gap — follow-up #88."
        )
        source_strategy: str | None = None
        dropped_alts: list[Any] = []
    else:
        source_strategy = decision.get("source_strategy")
        dropped_alts = decision.get("dropped_alternatives") or []
        _render_decision_card(decision)
    dashed = _dashed_sources_for_decision(bucket, dropped_alts)
    dot = _agent_graph_dot(
        bucket.get("strategy_spans", {}),
        composer_span,
        highlight_strategy=source_strategy,
        dashed_strategies=dashed,
        highlighted_key=canonical_key,
    )
    st.markdown("**Agent graph**")
    st.graphviz_chart(dot)
    _render_agent_node(
        "composer_v1",
        composer_span,
        bucket.get("llm_by_strategy", {}).get("composer_v1", []),
        expanded=False,
    )
    strategy_spans = bucket.get("strategy_spans", {})
    if not strategy_spans:
        st.caption("No upstream strategy spans recorded for this product.")
        return
    st.markdown("**Contributing agents**")
    for strategy in sorted(strategy_spans.keys()):
        _render_agent_node(
            strategy,
            strategy_spans[strategy],
            bucket.get("llm_by_strategy", {}).get(strategy, []),
            expanded=(strategy == source_strategy),
        )


def _render_decision_card(decision: dict[str, Any]) -> None:
    st.markdown("**Composer decision**")
    cols = st.columns(2)
    cols[0].markdown(f"**key**: `{decision.get('key')}`")
    cols[0].markdown(
        f"**source**: `{decision.get('source_strategy') or '(unknown)'}`"
    )
    val = decision.get("chosen_value")
    try:
        val_str = json.dumps(val, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        val_str = repr(val)
    cols[1].markdown("**chosen value**")
    cols[1].code(val_str[:400])
    reason = decision.get("reason")
    if reason:
        st.caption(f"**reason**: {reason}")
    dropped = decision.get("dropped_alternatives") or []
    if dropped:
        st.markdown(f"**dropped alternatives** ({len(dropped)})")
        st.json(_jsonable(dropped))


def _dashed_sources_for_decision(
    bucket: dict[str, Any], dropped_alts: list[Any]
) -> tuple[str, ...]:
    """Best-effort: infer which upstream strategies' findings matched a
    value in ``dropped_alternatives`` so the agent graph can render a
    dashed edge from those sources to composer_v1. We don't have
    per-drop ``source_strategy`` metadata in the current decision
    schema, so we reverse-lookup against each strategy's
    ``StrategyOutput.attributes`` values."""
    if not dropped_alts:
        return ()
    sources: set[str] = set()
    for strategy, span in bucket.get("strategy_spans", {}).items():
        if strategy not in _KNOWN_UPSTREAM_STRATEGIES:
            continue
        out = _span_output(span) or {}
        if not isinstance(out, dict):
            continue
        attrs = out.get("attributes") or {}
        if not isinstance(attrs, dict):
            continue
        flat_values: list[Any] = []
        for v in attrs.values():
            if isinstance(v, dict):
                flat_values.extend(v.values())
            elif isinstance(v, list):
                flat_values.extend(v)
            else:
                flat_values.append(v)
        for dv in dropped_alts:
            # ``in`` uses ``==``, which is mostly safe but can raise
            # ``TypeError`` when comparing mixed unorderable types
            # (e.g. a numpy scalar against a plain dict) — swallow
            # those and move on so one weird dropped_alt entry can't
            # take the whole side panel down (review fix #4, PR #86).
            try:
                if dv in flat_values:
                    sources.add(strategy)
                    break
            except TypeError:
                continue
    return tuple(sorted(sources))


def _render_cell_panel_finding(
    bucket: dict[str, Any],
    pid: str,
    strategy: str,
    key: str,
    value: Any,
) -> None:
    st.markdown("### Cell lineage")
    st.caption(
        f"`{strategy}.{key}`  ·  product `{_short_pid(pid)}`  ·  pre-composer finding"
    )
    if bucket.get("_pid_not_in_run"):
        st.warning(
            "This product isn't in the selected enrichment run — pick a "
            "newer run from the sidebar to see its findings."
        )
        return
    st.info(
        "This is a pre-composer finding — the raw output of one agent "
        "before the composer synthesized the canonical row. It may or "
        "may not have made it onto the canonical row."
    )
    # Cross-reference: did this exact value appear in any composer decision's
    # dropped_alternatives? If so, name the canonical key that shadowed it.
    composer_span = bucket.get("composer")
    if composer_span is not None:
        for canonical_key, decision in bucket.get("decisions_by_key", {}).items():
            dropped = decision.get("dropped_alternatives") or []
            # Same ``in``-triggers-TypeError guard as
            # _dashed_sources_for_decision; a single unorderable
            # dropped_alt entry must not break the panel.
            try:
                shadowed = value in dropped
            except TypeError:
                shadowed = False
            if shadowed:
                st.caption(
                    f"_Shadowed by `canonical.{canonical_key}` "
                    f"(composer chose `{decision.get('source_strategy')}` instead)._"
                )
                break
    span = bucket.get("strategy_spans", {}).get(strategy)
    if span is None:
        st.warning(f"No `{strategy}` span found for this product.")
        return
    _render_agent_node(
        strategy,
        span,
        bucket.get("llm_by_strategy", {}).get(strategy, []),
        expanded=True,
    )


def render_enriched_table(run: dict[str, Any], engine: Engine, merchant_id: str) -> None:
    st.markdown(
        f"### Enriched catalog for `{merchant_id}`  "
        f"(canonical columns from `composer_v1` + optional pre-composer findings)"
    )
    left, right = st.columns([3, 2], gap="medium")

    with left:
        limit = st.slider(
            "max rows", min_value=10, max_value=2000, value=200, step=10
        )
        # Clear the grid's cell selection when the findings toggle flips
        # — toggling changes the column layout (adds/removes
        # finding_cols), so a selection index captured before the flip
        # may point to a different cell after. The range check in
        # ``_render_side_panel`` guards against out-of-bounds, but the
        # *semantic* mismatch (user selected canonical.ram_gb, finding
        # column moves in, now they're looking at a parser_v1.* panel)
        # is only fixed by dropping the stale selection. Review
        # feedback #5, PR #86.
        toggle_key = f"enriched_findings_toggle_{merchant_id}"
        prev_toggle_key = f"_prev_{toggle_key}"
        grid_key = f"enriched_grid_{merchant_id}"
        show_findings = st.toggle(
            "Show pre-composer findings (debug)",
            value=False,
            key=toggle_key,
            help=(
                "Surface the raw `<strategy>.<key>` outputs that composer_v1 "
                "synthesized from. Useful for debugging why the composer "
                "chose one source over another."
            ),
        )
        prev = st.session_state.get(prev_toggle_key)
        if prev is not None and prev != show_findings:
            st.session_state.pop(grid_key, None)
        st.session_state[prev_toggle_key] = show_findings
        result = fetch_enriched_table(engine, merchant_id, limit=limit)
        if result is None:
            # Slug may have been rejected by the helper (malformed) or the
            # tables themselves are gone — keep the message generic so we
            # don't re-raise the same ValueError just to render copy.
            try:
                table_hint = f"`{merchant_catalog_table(merchant_id)}`"
            except ValueError:
                table_hint = f"the catalog table for `{merchant_id}`"
            st.warning(
                f"{table_hint} or its enriched table is missing — the merchant "
                "may have been deleted, or the id is malformed. Use **Refresh "
                "merchants** in the sidebar to re-hydrate the selector."
            )
            with right:
                _render_cell_panel_empty()
            return
        rows, raw_cols, canonical_cols, finding_cols = result
        if not canonical_cols:
            st.caption(
                f"{len(rows)} rows · {len(raw_cols)} raw columns · "
                f"**no `composer_v1` rows found** (run enrichment with the "
                "composer enabled — see PR #84)."
            )
        else:
            st.caption(
                f"{len(rows)} rows · {len(raw_cols)} raw columns · "
                f"{len(canonical_cols)} canonical columns"
                + (
                    f" · {len(finding_cols)} finding columns"
                    if show_findings
                    else ""
                )
            )
        display_cols = list(raw_cols) + list(canonical_cols)
        if show_findings:
            display_cols = display_cols + list(finding_cols)
        table = [{c: r.get(c) for c in display_cols} for r in rows]
        st.caption(
            "Canonical columns (`canonical.*`, green) are the composer's "
            "single-writer output. Click any canonical cell to see which "
            "agent produced its value, with that agent's full reasoning "
            "chain in the side panel."
        )
        canonical_set = set(canonical_cols)
        finding_set = set(finding_cols)
        try:
            import pandas as pd

            df = pd.DataFrame(table, columns=display_cols)

            def _tint(col: "pd.Series[Any]") -> list[str]:
                if col.name in canonical_set:
                    return ["background-color: rgba(45, 180, 130, 0.18)"] * len(col)
                if col.name in finding_set:
                    return ["background-color: rgba(140, 140, 140, 0.12)"] * len(col)
                return [""] * len(col)

            styled = df.style.apply(_tint, axis=0)
            # grid_key was set above (merchant-scoped) so switching
            # merchants / toggling findings both clear the selection
            # cleanly.
            event = st.dataframe(
                styled,
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode=["single-row", "single-column"],
                key=grid_key,
            )
        except ImportError:
            event = st.dataframe(
                table,
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode=["single-row", "single-column"],
                key=grid_key,
            )
        st.download_button(
            "Download as CSV",
            data=_rows_to_csv(table, display_cols),
            file_name=f"{merchant_id}_enriched.csv",
            mime="text/csv",
        )

    # --- Right column: cell-lineage side panel -------------------------
    with right:
        _render_side_panel(event, rows, display_cols, run)


def _render_side_panel(
    event: Any,
    rows: list[dict[str, Any]],
    display_cols: list[str],
    run: dict[str, Any],
) -> None:
    """Dispatch the right-column renderer based on the current selection.

    Split off so the ``with left:`` / ``with right:`` blocks stay readable;
    the routing itself is just the raw vs canonical vs finding fork."""
    sel = getattr(event, "selection", None) or {}
    if not isinstance(sel, dict):
        sel = dict(sel) if sel else {}
    sel_rows = sel.get("rows") or []
    sel_cols = sel.get("columns") or []
    if not (sel_rows and sel_cols):
        _render_cell_panel_empty()
        return
    row_idx = sel_rows[0]
    col_idx = sel_cols[0]
    # Streamlit returns the column index as an int (position) in some
    # versions and the column name as a str in others — handle both.
    if isinstance(col_idx, int):
        if col_idx < 0 or col_idx >= len(display_cols):
            _render_cell_panel_empty()
            return
        col_name = display_cols[col_idx]
    else:
        col_name = str(col_idx)
    if row_idx < 0 or row_idx >= len(rows):
        _render_cell_panel_empty()
        return
    row = rows[row_idx]
    pid = row.get("product_id")
    if pid is None:
        _render_cell_panel_empty()
        return
    pid_str = str(pid)

    # Route by column namespace.
    if col_name.startswith("canonical."):
        canonical_key = col_name[len("canonical.") :]
        bucket = _load_trace_bucket(run, pid_str)
        if bucket is None:
            _render_cell_panel_trace_missing(run, pid_str, col_name)
            return
        _render_cell_panel_canonical(bucket, pid_str, canonical_key)
        return
    for prefix in _KNOWN_UPSTREAM_STRATEGIES:
        if col_name.startswith(prefix + "."):
            bucket = _load_trace_bucket(run, pid_str)
            if bucket is None:
                _render_cell_panel_trace_missing(run, pid_str, col_name)
                return
            key = col_name[len(prefix) + 1 :]
            _render_cell_panel_finding(
                bucket, pid_str, prefix, key, row.get(col_name)
            )
            return
    _render_cell_panel_raw(col_name)


# run_id comes from ``summary.run_id`` which the orchestrator generates
# via ``uuid4().hex`` — 32 lowercase hex chars. We pin the regex to that
# shape before building a filesystem path so a malformed artifact (or
# one edited by hand) can't resolve into something like ``../../etc``
# (review fix: security note, PR #86). Anything else is treated as
# trace-missing.
_RUN_ID_SAFE_RE = re.compile(r"^[0-9a-f]{32}$")


def _load_trace_bucket(
    run: dict[str, Any], pid_str: str
) -> dict[str, Any] | None:
    """Return the per-product trace bucket for ``pid_str``, or ``None`` when
    the JSONL isn't present. Separate helper keeps the call-site routing
    compact and gives us a single place to apply the mtime-keyed cache.

    When the JSONL *is* present but ``pid_str`` simply didn't appear in
    this run — typically because the product was added to the catalog
    after the selected run artifact was produced — the returned bucket
    carries ``_pid_not_in_run = True`` so callers can differentiate
    "composer didn't run for this product" from "this product wasn't
    part of the selected run" (review feedback #3, PR #86)."""
    run_id = (run.get("summary") or {}).get("run_id")
    if not run_id or not _RUN_ID_SAFE_RE.match(str(run_id)):
        return None
    path = TRACES_DIR / f"{run_id}.jsonl"
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    index = _index_trace_by_product(str(path), mtime)
    bucket = index.get(pid_str)
    if bucket is None:
        return {
            "strategy_spans": {},
            "llm_by_strategy": {},
            "composer": None,
            "decisions_by_key": {},
            "_pid_not_in_run": True,
        }
    return bucket


def _render_cell_panel_trace_missing(
    run: dict[str, Any], pid_str: str, col_name: str
) -> None:
    run_id = (run.get("summary") or {}).get("run_id") or "?"
    st.markdown("### Cell lineage")
    st.caption(f"`{col_name}`  ·  product `{_short_pid(pid_str)}`")
    st.warning(
        f"No JSONL trace found at `logs/enrichment_traces/{run_id}.jsonl`. "
        "Re-run with `ENRICHMENT_TRACE_JSONL=1` to enable cell lineage."

    )


def _rows_to_csv(rows: list[dict[str, Any]], cols: list[str]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return buf.getvalue()


def render_kg_coverage(run: dict[str, Any]) -> None:
    summary = run.get("summary", {})
    cov = summary.get("kg_reader_coverage")
    st.markdown("### KG reader coverage")
    if cov is None:
        st.info("No coverage data in this run artifact.")
        return
    if cov.get("kg_built") is False:
        st.info(
            f"**KG not built yet** for "
            f"`({cov.get('merchant_id')}, {cov.get('kg_strategy')})` — "
            "tracked in #39 (KG-build-on-ingest is part of the umbrella "
            "issue; #61 is the separate decision about porting the retired "
            "`backfill_kg_features.py` heuristic into a strategy).\n\n"
            "Freshly-POSTed merchants land via `from_csv` without "
            "`:Product` nodes. Search will hit the positional fallback at "
            "`merchant_agent.py` until the KG is built."
        )
        return
    cols = st.columns(4)
    cols[0].metric("Reader refs", cov.get("referenced", 0))
    cols[1].metric("Producible", cov.get("producible", 0))
    cols[2].metric("Produced this run", cov.get("produced_this_run", 0))
    cols[3].metric("Missing", len(cov.get("missing", [])))
    missing = cov.get("missing", [])
    if missing:
        st.markdown("**Reader-referenced properties with no producer this run**")
        reserved = set(kg_projection.RESERVED_BOOL_FEATURES)
        st.dataframe(
            [
                {
                    "property": p,
                    "reserved (#61)": p in reserved,
                }
                for p in missing
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.download_button(
            "Download diff as JSON",
            data=json.dumps(cov, indent=2, default=str),
            file_name=f"kg_coverage_{cov.get('merchant_id')}.json",
            mime="application/json",
        )
    else:
        st.success(
            "Every reader-referenced property is producible by this run."
        )


def render_per_product(
    run: dict[str, Any], engine: Engine, merchant_id: str
) -> None:
    per_product = run.get("per_product_results", {})
    if not per_product:
        st.info(
            "This run artifact doesn't include `per_product_results`. "
            "Re-run with `scripts/run_enrichment.py --eval-output` from a "
            "revision that emits per-product results."
        )
        return
    pids = sorted(per_product.keys())
    pid = st.selectbox("product_id", pids)
    if not pid:
        return

    results = per_product[pid]
    st.markdown("**Agent results this run**")
    st.dataframe(
        [
            {
                "strategy": r.get("strategy"),
                "success": r.get("success"),
                "latency_ms": r.get("latency_ms"),
                "cost_usd": r.get("cost_usd"),
                "trace_id": r.get("trace_id"),
                "error": (r.get("error") or "")[:80],
            }
            for r in results
        ],
        hide_index=True,
        use_container_width=True,
    )

    try:
        raw_row, enriched_by_strategy = fetch_one_product(engine, merchant_id, pid)
    except (ProgrammingError, DatabaseError) as exc:
        st.warning(f"Catalog tables not found: {exc}")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("**Raw row**")
        if raw_row is None:
            st.warning("No raw row for this product_id.")
        else:
            st.json(_jsonable(raw_row))
    with right:
        st.markdown("**Enriched by strategy**")
        if not enriched_by_strategy:
            st.info("No enriched rows for this product yet.")
        else:
            st.json(_jsonable(enriched_by_strategy))

    st.markdown("**Projected :Product node (what the KG builder would write)**")
    if raw_row:
        identity = {
            k: raw_row.get(k) for k in kg_projection.IDENTITY_FIELDS if k in raw_row
        }
    else:
        identity = {}
    try:
        projected = kg_projection.project(identity, enriched_by_strategy)
    except Exception as exc:  # noqa: BLE001 - never break the tab
        st.warning(f"kg_projection.project failed: {exc}")
        projected = {}
    st.json(_jsonable(projected))


def _load_trace_jsonl(run_id: str) -> list[dict[str, Any]] | None:
    """Read ``logs/enrichment_traces/<run_id>.jsonl``.

    Returns the parsed span list, or ``None`` if the file doesn't exist
    (JSONL tracing is opt-in via ``ENRICHMENT_TRACE_JSONL=1``). Malformed
    lines are skipped with a caption-level note so a single corrupt span
    doesn't blank the whole tab.
    """
    path = TRACES_DIR / f"{run_id}.jsonl"
    if not path.exists():
        return None
    spans: list[dict[str, Any]] = []
    skipped = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        st.caption(f"_skipped {skipped} malformed trace line(s) in {path.name}_")
    return spans


def _span_output(span: dict[str, Any]) -> Any:
    """Final output for a span. ``tracing.py`` writes the result into the
    last entry of ``updates[]``, not the span's top-level ``output``, so a
    naive ``span.get('output')`` always returns ``None``. Walk updates in
    reverse and return the most recent non-empty output."""
    top = span.get("output")
    if top:
        return top
    for upd in reversed(span.get("updates") or []):
        if not isinstance(upd, dict):
            continue
        out = upd.get("output")
        # Empty dict/list are legitimate outputs (e.g. a specialist that
        # correctly decides "nothing to add"); only filter None and "".
        if out is not None and out != "":
            return out
    return None


def _span_metadata(span: dict[str, Any]) -> dict[str, Any]:
    """Aggregate metadata from all update events. Most LLM cost / token
    info lives there, not on the top-level span."""
    merged: dict[str, Any] = {}
    for upd in span.get("updates") or []:
        if isinstance(upd, dict) and isinstance(upd.get("metadata"), dict):
            merged.update(upd["metadata"])
    return merged


def _span_product_id(span: dict[str, Any]) -> str | None:
    """Extract product_id from a span's input or tags, if present."""
    inp = span.get("input") or {}
    if isinstance(inp, dict) and inp.get("product_id"):
        return str(inp["product_id"])
    for tag in span.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("product:"):
            return tag.split(":", 1)[1]
    return None


def _span_duration_ms(span: dict[str, Any]) -> float | None:
    """Compute span duration. ``tracing.py`` writes ``started_at`` / ``ended_at``
    as unix-epoch floats, but tolerate ISO strings too in case the writer
    format changes."""
    started = span.get("started_at")
    ended = span.get("ended_at")
    if started is None or ended is None:
        return None
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
        return round((ended - started) * 1000, 1)
    try:
        s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
        return round((e - s).total_seconds() * 1000, 1)
    except ValueError:
        return None


def render_reasoning_trace(run: dict[str, Any]) -> None:
    """Render JSONL agent traces for the current run.

    The runner writes spans to ``logs/enrichment_traces/<run_id>.jsonl`` when
    ``ENRICHMENT_TRACE_JSONL=1``. Spans carry the agent/strategy name, LLM
    calls (``llm:<model>``), inputs, outputs, and tags. This tab groups them
    by product and strategy so you can walk the decision path the agents
    took on each row.
    """
    summary = run.get("summary", {})
    run_id = summary.get("run_id")
    st.markdown("### Reasoning traces (per-agent spans)")
    if not run_id:
        st.info(
            "No `run_id` in this artifact — traces are keyed by run_id. "
            "Re-run with `--eval-output` from a recent revision."
        )
        return
    spans = _load_trace_jsonl(run_id)
    if spans is None:
        st.info(
            f"No JSONL trace file at `logs/enrichment_traces/{run_id}.jsonl`. "
            "Re-run with `ENRICHMENT_TRACE_JSONL=1` to enable JSONL tracing:\n\n"
            "`ENRICHMENT_TRACE_JSONL=1 python scripts/run_enrichment.py "
            "--merchant <id> --mode fixed --limit 5 --eval-output runs/x.json`"
        )
        return
    if not spans:
        st.warning("Trace file is empty.")
        return

    strategy_spans: list[dict[str, Any]] = []
    llm_by_strategy: dict[str, list[dict[str, Any]]] = {}
    other_spans: list[dict[str, Any]] = []
    known_strategies = set(
        enrichment_registry.list_strategies()
        if hasattr(enrichment_registry, "list_strategies")
        else []
    )
    # Fall back to the set of names that look like strategies when the
    # registry doesn't expose a listing helper.
    if not known_strategies:
        known_strategies = {
            s.get("name", "") for s in spans if not (s.get("name") or "").startswith("llm:")
        }

    current_strategy: str | None = None
    for span in spans:
        name = span.get("name") or ""
        if name.startswith("llm:"):
            key = current_strategy or "(unassigned)"
            llm_by_strategy.setdefault(key, []).append(span)
        elif name in known_strategies:
            current_strategy = name
            strategy_spans.append(span)
        else:
            other_spans.append(span)

    total_llm = sum(len(v) for v in llm_by_strategy.values())
    cols = st.columns(4)
    cols[0].metric("Spans", len(spans))
    cols[1].metric("Strategy spans", len(strategy_spans))
    cols[2].metric("LLM calls", total_llm)
    cols[3].metric("Other spans", len(other_spans))

    pids = sorted({pid for pid in (_span_product_id(s) for s in strategy_spans) if pid})
    pid_filter = st.selectbox(
        "Filter by product_id",
        ["(all)"] + pids,
        index=0,
        key="trace_pid_filter",
    )
    strategies = sorted({s.get("name") or "?" for s in strategy_spans})
    strat_filter = st.multiselect(
        "Filter by strategy",
        strategies,
        default=strategies,
        key="trace_strat_filter",
    )

    shown = 0
    for sp in strategy_spans:
        strategy = sp.get("name") or "?"
        if strategy not in strat_filter:
            continue
        pid = _span_product_id(sp)
        if pid_filter != "(all)" and pid != pid_filter:
            continue
        shown += 1
        dur = _span_duration_ms(sp)
        header = f"`{strategy}`"
        if pid:
            header += f"  ·  product `{pid[:8]}…`"
        if dur is not None:
            header += f"  ·  {dur} ms"
        tags = sp.get("tags") or []
        if tags:
            header += f"  ·  tags: {', '.join(tags[:3])}"
        with st.expander(header, expanded=False):
            left, right = st.columns(2)
            with left:
                st.markdown("**input**")
                st.json(_jsonable(sp.get("input") or {}))
            with right:
                st.markdown("**output**")
                st.json(_jsonable(_span_output(sp) or {}))
            md = _span_metadata(sp)
            if md:
                meta_bits = []
                if "cost_usd" in md:
                    meta_bits.append(f"cost: ${md['cost_usd']:.6f}")
                if "latency_ms" in md:
                    meta_bits.append(f"latency: {md['latency_ms']} ms")
                if "model" in md:
                    meta_bits.append(f"model: `{md['model']}`")
                if meta_bits:
                    st.caption("  ·  ".join(meta_bits))
            # Fallback debug view: the per-update payload for multi-step
            # span chains. The summary above only surfaces the final output
            # + aggregated metadata, which loses intermediate steps.
            updates = sp.get("updates") or []
            if updates:
                with st.expander(f"raw updates ({len(updates)})", expanded=False):
                    st.json(_jsonable(updates))
            llms = [
                llm for llm in llm_by_strategy.get(strategy, [])
                if _span_product_id(llm) in (None, pid) or pid is None
            ]
            if llms:
                st.markdown(f"**LLM calls within this strategy** ({len(llms)})")
                for llm in llms:
                    model = llm.get("name", "llm:?")
                    ldur = _span_duration_ms(llm)
                    label = f"{model}"
                    if ldur is not None:
                        label += f"  ·  {ldur} ms"
                    with st.expander(label, expanded=False):
                        li, lo = st.columns(2)
                        with li:
                            st.markdown("**prompt**")
                            st.json(_jsonable(llm.get("input") or {}))
                        with lo:
                            st.markdown("**response**")
                            st.json(_jsonable(_span_output(llm) or {}))
                        lmd = _span_metadata(llm)
                        if lmd:
                            tok_in = lmd.get("input_tokens", "?")
                            tok_out = lmd.get("output_tokens", "?")
                            st.caption(
                                f"tokens: {tok_in} in / {tok_out} out  ·  "
                                f"cost: ${lmd.get('cost_usd', 0):.6f}"
                            )

    if shown == 0:
        st.info("No strategy spans match the current filters.")

    if other_spans:
        with st.expander(f"Other spans ({len(other_spans)})", expanded=False):
            for sp in other_spans:
                st.markdown(f"**{sp.get('name', '?')}**")
                st.json(_jsonable(sp))


def render_coverage(engine: Engine, merchant_id: str) -> None:
    """Coverage tab — per-merchant cell breakdown by source_kind.

    Surfaces vision bullet 3: "% populated from parsing the raw source /
    % from crawling / % from parametric knowledge."  Powered by PR #97's
    ``source_kind`` field on ``ComposerDecision``.
    """
    st.markdown(f"### Coverage breakdown for `{merchant_id}`")
    st.caption(
        "Per-cell provenance across ``composer_v1`` decisions — counts how many "
        "canonical fields were produced by each ``source_kind`` bucket."
    )

    with st.spinner("Querying enriched table..."):
        cov = compute_coverage_breakdown(engine, merchant_id)

    # -- Zero-signal banner (current state on mocklaptops, see audit / Task 12)
    if cov.total_cells == 0:
        st.warning(
            "Composer is producing 0 cells across all products on this merchant. "
            "Coverage breakdown is structurally correct but reflects the upstream "
            "zero-signal issue (see ENRICHMENT_FAILURE_AUDIT.md / Task 12). "
            "Re-run after the model investigation lands."
        )

    # -- Top metrics row -------------------------------------------------------
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Products", cov.total_products)
    m2.metric("Cells populated", cov.total_cells)
    avg_cells = (
        round(cov.total_cells / cov.total_products, 1)
        if cov.total_products
        else 0.0
    )
    m3.metric("Avg cells / product", avg_cells)
    m4.metric("Rows with incomplete_decisions", cov.incomplete_decisions_count)

    st.divider()

    # -- Source-kind bar chart -------------------------------------------------
    st.markdown("#### Per-cell provenance distribution")
    st.caption("Counts how many composer cells came from each source_kind bucket.")

    if cov.by_source_kind:
        sk_df = pd.DataFrame(
            [{"source_kind": k, "cells": v}
             for k, v in sorted(cov.by_source_kind.items(), key=lambda x: -x[1])],
        ).set_index("source_kind")
        st.bar_chart(sk_df, use_container_width=True)
    else:
        st.info("No source_kind data yet — run enrichment to populate this chart.")

    st.divider()

    # -- Per-attribute breakdown table ----------------------------------------
    st.markdown("#### Per-attribute coverage (sorted by gap: worst first)")
    st.caption(
        "Rows = attribute names.  Columns = source_kind bucket counts, "
        "``missing`` = products where this attribute was absent, "
        "``total_populated`` = filled cells, ``pct_populated`` = fill rate."
    )

    if cov.by_attribute:
        all_sk = sorted(
            {sk for sks in cov.by_attribute.values() for sk in sks}
            | (set(cov.by_source_kind.keys()) if cov.by_source_kind else set())
        )
        attr_rows: list[dict[str, Any]] = []
        for attr in sorted(cov.by_attribute.keys()):
            row: dict[str, Any] = {"attribute": attr}
            total_pop = 0
            for sk in all_sk:
                cnt = cov.by_attribute[attr].get(sk, 0)
                row[sk] = cnt
                total_pop += cnt
            missing_cnt = cov.missing_per_attribute.get(attr, 0)
            row["missing"] = missing_cnt
            row["total_populated"] = total_pop
            denom = total_pop + missing_cnt
            row["pct_populated"] = round(total_pop / denom * 100, 1) if denom else 0.0
            attr_rows.append(row)

        attr_df = pd.DataFrame(attr_rows).sort_values(
            "pct_populated", ascending=True
        )
        st.dataframe(attr_df, hide_index=True, use_container_width=True)

        st.download_button(
            "Download as CSV",
            data=attr_df.to_csv(index=False),
            file_name=f"{merchant_id}_coverage.csv",
            mime="text/csv",
        )
    else:
        st.info("No composed attributes found — the attribute table will appear once enrichment runs.")

    st.divider()

    # -- Optional: feature-discovery overlay (PR #91) -------------------------
    fd_cov = _latest_feature_discovery_coverage()
    if fd_cov is not None:
        covered = fd_cov.get("covered", 0)
        missing_fd = fd_cov.get("missing", 0)
        underused = fd_cov.get("underused", 0)
        denom_fd = covered + missing_fd + underused
        pct_fd = round(covered / denom_fd * 100, 1) if denom_fd else 0.0
        st.markdown("#### Feature-discovery overlay (PR #91)")
        st.metric(
            "% of user-queried features the catalog covers",
            f"{pct_fd}%",
            help=(
                f"covered={covered}, missing={missing_fd}, underused={underused} "
                "(from latest runs/feature_discovery coverage.json)"
            ),
        )


def _jsonable(obj: Any) -> Any:
    try:
        json.dumps(obj, default=str)
        return obj
    except TypeError:
        return json.loads(json.dumps(obj, default=str))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Enrichment Inspector",
        page_icon=":mag:",
        layout="wide",
    )
    st.title("Enrichment Inspector")
    st.caption(
        "Raw JSONB the merchant agent saw → enriched features it derived → "
        "KG node properties it flattens onto. Deep-links into Langfuse when "
        "`LANGFUSE_HOST` is set."
    )

    engine = get_engine()

    # Sidebar — merchant + run selectors.
    with st.sidebar:
        st.header("Selectors")
        if st.button("Refresh merchants"):
            st.cache_resource.clear()
            st.cache_data.clear()
            st.rerun()
        try:
            merchants = list_merchants(engine)
        except (ProgrammingError, DatabaseError) as exc:
            st.error(
                "Could not read merchants.registry — has migration 005 been "
                f"applied?\n\n{exc}"
            )
            st.stop()
        if not merchants:
            st.warning("No rows in merchants.registry yet.")
            st.stop()
        merchant_ids = [m["merchant_id"] for m in merchants]
        default_idx = merchant_ids.index("default") if "default" in merchant_ids else 0
        picked_id = st.selectbox(
            "catalog",
            merchant_ids,
            index=default_idx,
            key="merchant_pick",
            help="One catalog per merchant (one row in merchants.registry).",
        )
        picked = next(m for m in merchants if m["merchant_id"] == picked_id)
        st.caption(
            f"domain: `{picked.get('domain')}`  ·  "
            f"strategy: `{picked.get('strategy')}`  ·  "
            f"kg_strategy: `{picked.get('kg_strategy')}`"
        )
        st.caption(
            f"catalog_size: **{picked.get('catalog_size')}**  ·  "
            f"created_at: `{picked.get('created_at')}`"
        )

        st.divider()
        artifacts = list_run_artifacts_for_merchant(picked_id)
        if not artifacts:
            st.info(
                f"No enrichment runs yet for `{picked_id}`. Generate one "
                "with:\n\n"
                "`python scripts/run_enrichment.py --mode fixed "
                f"--merchant {picked_id} --limit 5 "
                f"--eval-output runs/{picked_id}.json`"
            )
            run: dict[str, Any] = {"summary": {}, "assessment": {}, "catalog_schema": {}}
        else:
            labels = [f"{p.name} ({_fmt_mtime(p)})" for p in artifacts]
            pick = st.selectbox("Enrichment run", labels, index=0)
            chosen = artifacts[labels.index(pick)]
            run = load_run(str(chosen))
            st.caption(f"loaded `{chosen}`")

    tabs = st.tabs(
        [
            "Run summary",
            "Raw → Enriched → KG",
            "Enriched table",
            "KG coverage",
            "Coverage",
            "Per-product drill-down",
            "Reasoning trace",
        ]
    )
    with tabs[0]:
        render_run_summary(run)
    with tabs[1]:
        render_raw_enriched_kg(run, engine, picked_id)
    with tabs[2]:
        render_enriched_table(run, engine, picked_id)
    with tabs[3]:
        render_kg_coverage(run)
    with tabs[4]:
        render_coverage(engine, picked_id)
    with tabs[5]:
        render_per_product(run, engine, picked_id)
    with tabs[6]:
        render_reasoning_trace(run)


def _fmt_mtime(path: Path) -> str:
    mt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return mt.isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
