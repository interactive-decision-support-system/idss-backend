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
    * app.kg_projection — IDENTITY_FIELDS, FLATTENING_RULES, KEY_PATTERNS,
        cypher_referenced_properties() for the KG tier and coverage metric.

Env vars:
    * DATABASE_URL            — same connection the mcp-server uses.
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
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

# Make the mcp-server package importable so we can reuse kg_projection /
# registry statics without duplicating them here.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "mcp-server"))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError, ProgrammingError

from app import kg_projection
from app.enrichment import registry as enrichment_registry
from app.merchant_agent import merchant_catalog_table, merchant_enriched_table


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


def langfuse_tag_url(run_id: str) -> str:
    q = urllib.parse.urlencode({"tags": f"run:{run_id}"})
    return f"{LANGFUSE_HOST}/traces?{q}"


# ---------------------------------------------------------------------------
# Catalog queries (raw + enriched join)
# ---------------------------------------------------------------------------


def fetch_enriched_table(
    engine: Engine, merchant_id: str, limit: int = 500
) -> tuple[list[dict[str, Any]], list[str], list[str]] | None:
    """Join raw products and enriched attributes for one merchant, pivoted
    into ``<strategy>.<key>`` columns so every raw column sits next to the
    features the agents derived from it. Returns
    ``(rows, raw_columns, enriched_columns)`` or ``None`` if the merchant's
    tables are gone (DELETE /merchant mid-session) **or** the slug doesn't
    match ``MERCHANT_ID_RE`` (stale URL param, malformed test fixture, any
    future path that bypasses the registry-backed selector)."""
    # Route through the canonical helpers so a future schema-per-tenant
    # move (issue #38) only has to touch ``merchant_*_table``. The helpers
    # also re-validate the slug against MERCHANT_ID_RE — interpolation
    # below is safe by construction.
    try:
        raw = merchant_catalog_table(merchant_id)
        enr = merchant_enriched_table(merchant_id)
    except ValueError:
        return None
    sql = text(
        f"""
        SELECT p.product_id, p.name, p.brand, p.category, p.price,
               p.attributes AS raw_attributes,
               e.strategy, e.attributes AS enriched_attributes
        FROM {raw} p
        LEFT JOIN {enr} e USING (product_id, merchant_id)
        ORDER BY p.product_id
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
    enriched_cols: set[str] = set()
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
        if strategy and isinstance(attrs, dict):
            for k, v in attrs.items():
                col = f"{strategy}.{k}"
                enriched_cols.add(col)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False, default=str)[:160]
                by_pid[pid][col] = v

    rows = list(by_pid.values())
    return rows, raw_cols, sorted(enriched_cols)


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
        rr = conn.execute(
            text(f"SELECT * FROM {raw} WHERE product_id = :pid LIMIT 1"),
            {"pid": product_id},
        ).mappings().one_or_none()
        raw_row = dict(rr) if rr else None
        enriched_rows = conn.execute(
            text(
                f"SELECT strategy, attributes FROM {enr} "
                f"WHERE product_id = :pid"
            ),
            {"pid": product_id},
        ).mappings().all()
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


def render_enriched_table(engine: Engine, merchant_id: str) -> None:
    st.markdown(
        f"### Enriched catalog for `{merchant_id}`  "
        f"(raw columns + `<strategy>.<key>` derived features)"
    )
    limit = st.slider("max rows", min_value=10, max_value=2000, value=200, step=10)
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
        return
    rows, raw_cols, enriched_cols = result
    st.caption(
        f"{len(rows)} rows · {len(raw_cols)} raw columns · "
        f"{len(enriched_cols)} derived columns"
    )
    cols = raw_cols + enriched_cols
    table = [{c: r.get(c) for c in cols} for r in rows]
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.download_button(
        "Download as CSV",
        data=_rows_to_csv(table, cols),
        file_name=f"{merchant_id}_enriched.csv",
        mime="text/csv",
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
                st.json(_jsonable(sp.get("output") or {}))
            updates = sp.get("updates") or []
            if updates:
                st.markdown(f"**updates** ({len(updates)})")
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
                            st.json(_jsonable(llm.get("output") or {}))

    if shown == 0:
        st.info("No strategy spans match the current filters.")

    if other_spans:
        with st.expander(f"Other spans ({len(other_spans)})", expanded=False):
            for sp in other_spans:
                st.markdown(f"**{sp.get('name', '?')}**")
                st.json(_jsonable(sp))


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
            "merchant",
            merchant_ids,
            index=default_idx,
            key="merchant_pick",
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
        artifacts = list_run_artifacts()
        if not artifacts:
            st.info(
                "No runs yet. Generate one with:\n\n"
                "`python scripts/run_enrichment.py "
                "--mode fixed --limit 5 --eval-output runs/dev.json`"
            )
            run: dict[str, Any] = {"summary": {}, "assessment": {}, "catalog_schema": {}}
        else:
            labels = [f"{p.name} ({_fmt_mtime(p)})" for p in artifacts]
            pick = st.selectbox("run artifact", labels, index=0)
            chosen = artifacts[labels.index(pick)]
            run = load_run(str(chosen))
            st.caption(f"loaded `{chosen}`")

    tabs = st.tabs(
        [
            "Run summary",
            "Raw → Enriched → KG",
            "Enriched table",
            "KG coverage",
            "Per-product drill-down",
            "Reasoning trace",
        ]
    )
    with tabs[0]:
        render_run_summary(run)
    with tabs[1]:
        render_raw_enriched_kg(run, engine, picked_id)
    with tabs[2]:
        render_enriched_table(engine, picked_id)
    with tabs[3]:
        render_kg_coverage(run)
    with tabs[4]:
        render_per_product(run, engine, picked_id)
    with tabs[5]:
        render_reasoning_trace(run)


def _fmt_mtime(path: Path) -> str:
    mt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return mt.isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
