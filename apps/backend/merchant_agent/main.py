"""FastAPI surface for the standalone agentic-ecommerce backend."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from merchant_agent.contract import Offer, StructuredQuery
from merchant_agent.database import engine, get_db
from merchant_agent.endpoints import search_products
from merchant_agent.ingestion.schema import drop_merchant_catalog
from merchant_agent.merchant_agent import MerchantAgent, validate_merchant_id
from merchant_agent.schemas import SearchProductsRequest, SearchProductsResponse

logger = logging.getLogger(__name__)

merchants: dict[str, MerchantAgent] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        merchants["default"] = MerchantAgent(
            merchant_id="default",
            domain="electronics",
            strategy="normalizer_v1",
            kg_strategy="default_v1",
        )
    except Exception as exc:
        logger.warning("default merchant warmup failed: %s", exc)
    yield


app = FastAPI(
    title="agentic-ecommerce backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "agentic-ecommerce backend", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


def _get_or_hydrate_merchant(merchant_id: str, db: Session) -> MerchantAgent:
    from merchant_agent.catalog import CatalogNotFound

    agent = merchants.get(merchant_id)
    if agent is not None:
        return agent

    row = db.execute(
        sa_text(
            "SELECT merchant_id, domain, strategy, kg_strategy "
            "FROM merchants.registry WHERE merchant_id = :mid"
        ),
        {"mid": merchant_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Merchant '{merchant_id}' not found",
        )

    try:
        agent = MerchantAgent.open(
            merchant_id=row["merchant_id"],
            db=db,
            domain=row["domain"],
            strategy=row["strategy"],
            kg_strategy=row["kg_strategy"],
        )
    except CatalogNotFound as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"merchant '{merchant_id}' is registered but its catalog "
                f"table {exc.missing_table} is missing"
            ),
        )

    merchants[merchant_id] = agent
    return agent


@app.post("/api/search-products", response_model=SearchProductsResponse)
async def api_search_products(
    request: SearchProductsRequest,
    db: Session = Depends(get_db),
) -> SearchProductsResponse:
    return await search_products(request, db)


@app.post("/merchant/search", response_model=list[Offer])
async def merchant_search(
    query: StructuredQuery,
    db: Session = Depends(get_db),
) -> list[Offer]:
    agent = _get_or_hydrate_merchant("default", db)
    return await agent.search(query, db)


@app.post("/merchant/{merchant_id}/search", response_model=list[Offer])
async def merchant_search_by_id(
    merchant_id: str,
    query: StructuredQuery,
    db: Session = Depends(get_db),
) -> list[Offer]:
    agent = _get_or_hydrate_merchant(merchant_id, db)
    return await agent.search(query, db)


@app.get("/merchant/{merchant_id}/health")
def merchant_health(merchant_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    agent = _get_or_hydrate_merchant(merchant_id, db)
    return agent.health(db)


def _catalog_row_count(db: Session, merchant_id: str) -> int:
    validate_merchant_id(merchant_id)
    try:
        return int(
            db.execute(
                sa_text(f"SELECT COUNT(*) FROM merchants.products_{merchant_id}")
            ).scalar()
            or 0
        )
    except Exception as exc:
        logger.warning("catalog_count_failed merchant=%s err=%s", merchant_id, exc)
        db.rollback()
        return 0


@app.post("/merchant", status_code=status.HTTP_201_CREATED)
def create_merchant(
    file: UploadFile = File(...),
    merchant_id: str = Form(...),
    domain: str = Form(...),
    product_type: str = Form(...),
    strategy: str = Form("normalizer_v1"),
    kg_strategy: str = Form("default_v1"),
    col_map: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        validate_merchant_id(merchant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    db.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtext(:mid))"),
        {"mid": merchant_id},
    )
    existing = db.execute(
        sa_text("SELECT 1 FROM merchants.registry WHERE merchant_id = :mid"),
        {"mid": merchant_id},
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Merchant '{merchant_id}' already exists. DELETE it before re-posting.",
        )

    parsed_col_map: dict[str, str] | None = None
    if col_map:
        try:
            parsed = json.loads(col_map)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"col_map is not valid JSON: {exc}")
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise HTTPException(
                status_code=400,
                detail="col_map must be a JSON object of string to string",
            )
        parsed_col_map = parsed

    contents = file.file.read()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(contents)
        try:
            agent = MerchantAgent.from_csv(
                tmp_path,
                merchant_id=merchant_id,
                domain=domain,
                product_type=product_type,
                strategy=strategy,
                kg_strategy=kg_strategy,
                col_map=parsed_col_map,
            )
        except Exception as exc:
            try:
                cleanup_conn = engine.raw_connection()
                try:
                    drop_merchant_catalog(merchant_id, cleanup_conn, _force=True)
                finally:
                    cleanup_conn.close()
            except Exception as cleanup_exc:
                logger.warning(
                    "create_merchant_cleanup_failed id=%s err=%s",
                    merchant_id,
                    cleanup_exc,
                )
            raise HTTPException(
                status_code=500,
                detail=f"merchant provisioning failed: {exc}",
            )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    merchants[agent.merchant_id] = agent
    return {
        "merchant_id": agent.merchant_id,
        "onboarding_state": "ready",
        "catalog_size": _catalog_row_count(db, agent.merchant_id),
    }


@app.get("/merchant")
def list_merchants(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.execute(
        sa_text(
            "SELECT merchant_id, domain, strategy, kg_strategy, created_at "
            "FROM merchants.registry ORDER BY created_at"
        )
    ).mappings().all()
    return [
        {
            "merchant_id": row["merchant_id"],
            "domain": row["domain"],
            "strategy": row["strategy"],
            "kg_strategy": row["kg_strategy"],
            "catalog_size": _catalog_row_count(db, row["merchant_id"]),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


@app.delete("/merchant/{merchant_id}")
def delete_merchant(
    merchant_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        validate_merchant_id(merchant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    existing = db.execute(
        sa_text("SELECT 1 FROM merchants.registry WHERE merchant_id = :mid"),
        {"mid": merchant_id},
    ).first()
    if existing is None:
        raise HTTPException(status_code=404, detail=f"merchant '{merchant_id}' not found")

    if os.environ.get("ALLOW_MERCHANT_DROP", "") != "1":
        raise HTTPException(
            status_code=403,
            detail="drop_merchant_catalog disabled. Set ALLOW_MERCHANT_DROP=1 to enable.",
        )

    db.execute(
        sa_text("DELETE FROM merchants.registry WHERE merchant_id = :mid"),
        {"mid": merchant_id},
    )
    db.commit()

    raw_conn = engine.raw_connection()
    try:
        drop_merchant_catalog(merchant_id, raw_conn)
    finally:
        raw_conn.close()

    merchants.pop(merchant_id, None)
    return {"merchant_id": merchant_id, "deleted": True}
