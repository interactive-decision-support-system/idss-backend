"""Catalog binding — the per-merchant table tuple, with two construction modes.

A ``Catalog`` is the concrete answer to "which tables does this merchant own?":
fully-qualified raw and enriched table names, plus the per-merchant SQLAlchemy
models bound to them. There are two ways to obtain one:

  * ``Catalog.for_merchant(merchant_id)`` — derive names from the slug. Cheap,
    no DB round-trip, no existence guarantee. The right shape for the lifespan
    bootstrap, ``from_csv`` (which is *about* to create the tables), and unit
    tests that build agents against fixtures they already control.

  * ``open_catalog(merchant_id, db)`` — derive names AND probe Postgres for
    the raw table. Raises ``CatalogNotFound`` when the slug has no physical
    tables. The right shape for the request hot path: a registry row pointing
    at missing tables is a deploy-skew bug we want surfaced loudly, not a
    silent "the agent will explode on first query" failure.

This split exists because a constructor that always probes the DB makes
``MerchantAgent`` un-instantiatable in unit tests, and a constructor that
never probes lets ``MerchantAgent('ghost', 'books')`` succeed and produce a
broken agent that 500s on first search. Pushing verification out of the
constructor and into a named factory keeps both call patterns honest.

The Catalog never re-derives names from ``merchant_id`` — once constructed, it
is the source of truth for table identity. ``MerchantAgent`` reads
``catalog.raw_table`` / ``catalog.enriched_table`` rather than re-running the
``f"merchants.products_{merchant_id}"`` formula, so a future schema-per-tenant
move (issue #38) only has to touch the factories here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from merchant_agent.merchant_agent import (
    merchant_catalog_table,
    merchant_enriched_table,
    validate_merchant_id,
)
from merchant_agent.models import make_enriched_model, make_product_model


class CatalogNotFound(LookupError):
    """Raised when a merchant id has no backing physical tables.

    Carries the merchant id and the table name we probed so the caller can
    surface enough context to debug a registry-vs-DDL skew without a second
    SELECT.
    """

    def __init__(self, merchant_id: str, missing_table: str) -> None:
        super().__init__(
            f"no catalog found for merchant_id={merchant_id!r}: "
            f"{missing_table} does not exist"
        )
        self.merchant_id = merchant_id
        self.missing_table = missing_table


@dataclass(frozen=True, eq=False)
class Catalog:
    """Immutable per-merchant table binding.

    Equality is defined explicitly on ``merchant_id`` (not the dataclass
    default of all-fields). The table names and models are deterministic
    functions of the slug, so two Catalogs for the same merchant are
    interchangeable — but the model identity only happens to coincide today
    because ``make_product_model`` / ``make_enriched_model`` are cached in
    ``merchant_agent.models``. If that cache is ever scoped per-session or removed,
    field-wise equality would silently break. Pinning equality to
    ``merchant_id`` here makes the intent the contract.

    ``frozen=True`` keeps the agent free to stash this on ``self`` without
    callers worrying about mutation.
    """

    merchant_id: str
    raw_table: str          # "merchants.products_<id>"
    enriched_table: str     # "merchants.products_enriched_<id>"
    product_model: Any      # SQLAlchemy class for the raw table
    enriched_model: Any     # SQLAlchemy class for the enriched table

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Catalog):
            return NotImplemented
        return self.merchant_id == other.merchant_id

    def __hash__(self) -> int:
        return hash(("Catalog", self.merchant_id))

    @classmethod
    def for_merchant(cls, merchant_id: str) -> "Catalog":
        """Build a Catalog from the slug alone — no DB round-trip.

        Use this when you already know the tables exist (lifespan bootstrap,
        post-from_csv) or when you don't care because you're constructing a
        test double. For request-time hydration go through ``open_catalog``
        instead so a missing table fails loudly rather than at first query.
        """
        merchant_id = validate_merchant_id(merchant_id)
        return cls(
            merchant_id=merchant_id,
            raw_table=merchant_catalog_table(merchant_id),
            enriched_table=merchant_enriched_table(merchant_id),
            product_model=make_product_model(merchant_id),
            enriched_model=make_enriched_model(merchant_id),
        )


def open_catalog(merchant_id: str, db: Session) -> Catalog:
    """Verify and return the Catalog for ``merchant_id``.

    Probes ``to_regclass`` against the raw table — a single planner call that
    returns NULL when the relation is missing rather than raising, so the
    surrounding transaction stays usable for the next query. The enriched
    table is not probed: it is created in lock-step with raw by
    ``create_merchant_catalog``, so probing both would just double the
    round-trip without catching new failure modes.

    Raises ``CatalogNotFound`` when the raw table is missing. Callers should
    map that to 500 rather than 404 — by the time we are calling
    ``open_catalog`` the registry already vouched for this merchant, so a
    missing table is a deploy-skew bug, not "unknown merchant".
    """
    merchant_id = validate_merchant_id(merchant_id)
    raw = merchant_catalog_table(merchant_id)
    # ``to_regclass`` is the right primitive here: it accepts a fully-qualified
    # name as text and returns NULL on miss, no exception, no transaction
    # rollback. Cheaper than a COUNT(*) and immune to permission edge cases
    # that would make a SELECT raise.
    found = db.execute(
        text("SELECT to_regclass(:fqn)"),
        {"fqn": raw},
    ).scalar()
    if found is None:
        raise CatalogNotFound(merchant_id=merchant_id, missing_table=raw)
    return Catalog.for_merchant(merchant_id)


__all__ = ["Catalog", "CatalogNotFound", "open_catalog"]
