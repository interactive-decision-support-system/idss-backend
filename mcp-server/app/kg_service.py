"""
Knowledge Graph Service for Electronics Products.

Implements Neo4j integration for complex constraint-based queries.
Focuses on electronics (laptops, components, compatibility).

Per week4notes.txt:
- Knowledge graph for verification and reasoning
- Handle complex queries like "gaming PC with components X, Y, Z under budget B"
- Graph algorithms for constraint satisfaction
- Real electronics data (not synthetic)
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import logging

from app.kg_projection import TAG_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)

try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False
    logger.warning("neo4j driver not installed. Install with: pip install neo4j")


class KnowledgeGraphService:
    """
    Knowledge Graph service for electronics product relationships.
    
    Uses Neo4j to model:
    - Product relationships (compatibility, alternatives, bundles)
    - Component relationships (CPU-GPU-RAM compatibility)
    - Brand relationships (product lines, series)
    - Use case relationships (gaming, video editing, work)
    - Price-performance relationships
    
    Per week4notes.txt: KG supports verification, reasoning, and complex queries.
    """
    
    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: Optional[str] = None):
        """
        Initialize Neo4j connection.
        
        Args:
            uri: Neo4j bolt URI (or set NEO4J_URI env).
            user: Neo4j user (or set NEO4J_USER env).
            password: Neo4j password — set via NEO4J_PASSWORD env only; do not hardcode.
        """
        if not NEO4J_AVAILABLE:
            self.driver = None
            logger.warning("Neo4j not available - KG features disabled")
            return
        if not password or not password.strip():
            self.driver = None
            logger.warning("NEO4J_PASSWORD not set - KG disabled. Set in .env (do not commit .env).")
            return
        try:
            # connection_timeout=2 caps cold-start TCP hang to 2 s instead of 30 s.
            self.driver = GraphDatabase.driver(
                uri, auth=(user, password), connection_timeout=2
            )
            # Test connection
            with self.driver.session() as session:
                session.run("RETURN 1")
            logger.info("Connected to Neo4j knowledge graph")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            self.driver = None
    
    def close(self):
        """Close Neo4j connection."""
        if self.driver:
            self.driver.close()
    
    def is_available(self) -> bool:
        """Check if KG is available."""
        return self.driver is not None
    
    def search_candidates(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
        *,
        merchant_id: Optional[str] = None,
        kg_strategy: Optional[str] = None,
    ) -> Tuple[List[str], Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """
        Search for product candidates using knowledge graph.

        The KG stores one node per ``(product_id, merchant_id, kg_strategy)``
        triple — mirroring the ``(merchant_id, strategy)`` key in
        products_enriched. Call sites MUST pass the agent's merchant /
        strategy pair or results cross-leak between tenants. ``None`` is
        accepted for backwards compatibility with pre-#52 call sites; the
        resulting Cypher short-circuits the tenancy filter.

        Args:
            query: Natural language query (e.g., "gaming laptop for video editing")
            filters: Structured filters (category, price_max, brand, etc.)
            limit: Maximum number of candidates to return
            merchant_id: Tenant scope. ``None`` → legacy unfiltered.
            kg_strategy: Enrichment strategy this KG was built from. ``None``
                → legacy unfiltered.

        Returns:
            Tuple of ``(product_ids, scores, explanation_path)``:
            - ``product_ids``: candidate product IDs in rank order.
            - ``scores``: ``{product_id: {"score": float, "breakdown":
              {"soft": ..., "phrase": ..., "token": ..., "connectivity": ...}}}``.
              Per-term contributions are for Offer.score_breakdown (issue #52
              §4.D). Opaque payload — the shopping agent must not branch on
              internals.
            - ``explanation_path``: Graph traversal explanation for debugging.
        """
        if not self.is_available():
            return [], {}, {}

        try:
            with self.driver.session() as session:
                cypher_query = self._build_cypher_query(
                    query, filters, limit,
                    merchant_id=merchant_id, kg_strategy=kg_strategy,
                )
                params = {
                    "limit": limit,
                    # Soft-tag confidence cutoff is a parameter, not a literal
                    # in the Cypher string, so we can recalibrate it without a
                    # query rebuild. See issue #60 for calibration tracking.
                    "tag_threshold": float(TAG_CONFIDENCE_THRESHOLD),
                    "merchant_id": merchant_id,
                    "kg_strategy": kg_strategy,
                    **self._extract_filters(filters or {}),
                }
                if query and len(query) >= 2:
                    params["q"] = query.lower()[:50]
                    tokens = self._tokenize_query(query)
                    if len(tokens) >= 2:
                        for i, tok in enumerate(tokens):
                            params[f"q_tok_{i}"] = tok
                result = session.run(cypher_query, params)

                product_ids: List[str] = []
                scores: Dict[str, Dict[str, Any]] = {}
                explanation_path: Dict[str, Any] = {
                    "query": query,
                    "filters": filters,
                    "merchant_id": merchant_id,
                    "kg_strategy": kg_strategy,
                    "traversal": [],
                }

                for record in result:
                    product_id = record.get("product_id")
                    if not product_id:
                        continue
                    product_ids.append(product_id)
                    breakdown = {
                        "soft": float(record.get("soft_score") or 0.0),
                        "phrase": float(record.get("phrase_score") or 0.0),
                        "token": float(record.get("token_score") or 0.0),
                        "connectivity": float(record.get("connectivity_score") or 0.0),
                    }
                    total = sum(breakdown.values())
                    scores[product_id] = {"score": total, "breakdown": breakdown}
                    explanation_path["traversal"].append({
                        "product_id": product_id,
                        "score": total,
                        "breakdown": breakdown,
                        "path": record.get("path", ""),
                    })

                logger.info(f"KG search found {len(product_ids)} candidates for query: {query[:50]}")
                return product_ids, scores, explanation_path

        except Exception as e:
            logger.error(f"KG search failed: {e}", exc_info=True)
            return [], {}, {"error": str(e)}
    
    @staticmethod
    def _tokenize_query(query: str, max_tokens: int = 12) -> List[str]:
        """Lowercase, whitespace-split, strip light punctuation, dedupe.

        Only tokens ≥ 2 chars are kept; order is preserved for deterministic
        Cypher parameter naming ($q_tok_0, $q_tok_1, …). Capped so a pathological
        input doesn't generate a Cypher query with hundreds of CASE WHENs.
        """
        if not query:
            return []
        seen: List[str] = []
        for raw in query.split():
            t = raw.strip(".,;:!?\"'()[]{}").lower()
            if len(t) >= 2 and t not in seen:
                seen.append(t)
                if len(seen) >= max_tokens:
                    break
        return seen

    def _build_cypher_query(
        self,
        query: str,
        filters: Optional[Dict[str, Any]],
        limit: int,
        *,
        merchant_id: Optional[str] = None,
        kg_strategy: Optional[str] = None,
    ) -> str:
        """
        Build Cypher query for product search with hard and soft constraints.

        Tenant scope (leading WHERE): ``p.merchant_id = $merchant_id AND
        p.kg_strategy = $kg_strategy`` when both args are set. Mirrors the
        ``(merchant_id, strategy)`` composite key on products_enriched.
        Either arg set to ``None`` skips that condition — legacy callers
        get unfiltered behaviour.

        Hard constraints (WHERE clause): category, price bounds, brand, subcategory,
            repairable, refurbished, battery_life_min.  Products that fail these are
            excluded entirely — they are non-negotiable requirements.

        Soft constraints (scoring): use-case flags (good_for_gaming, good_for_ml, …)
            and text match.  Products that don't have these flags are still shown but
            ranked lower.  This preserves recall when the KG hasn't been fully
            back-filled with use-case attributes.

        Connectivity bonus: products with more SIMILAR_TO outgoing edges are ranked
            higher, acting as a graph-centrality signal for popular/well-connected items.
        """
        # ── Hard constraints (WHERE) ─────────────────────────────────────────
        category = (filters or {}).get("category", "Electronics")
        hard_conditions: List[str] = []
        # Tenancy scope — prepended so every scan short-circuits on the index
        # before any other predicate runs.
        if merchant_id is not None:
            hard_conditions.append("p.merchant_id = $merchant_id")
        if kg_strategy is not None:
            hard_conditions.append("p.kg_strategy = $kg_strategy")
        hard_conditions.append("p.category = $category")

        if filters:
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                hard_conditions.append("p.brand = $brand")
            if filters.get("subcategory"):
                hard_conditions.append("p.subcategory = $subcategory")
            if filters.get("price_max") is not None or filters.get("price_max_cents") is not None:
                hard_conditions.append("p.price <= $price_max")
            if filters.get("price_min") is not None or filters.get("price_min_cents") is not None:
                hard_conditions.append("p.price >= $price_min")
            # Explicit equipment/condition requirements stay hard
            if filters.get("repairable"):
                hard_conditions.append("p.repairable = true")
            if filters.get("refurbished"):
                hard_conditions.append("p.refurbished = true")
            if filters.get("battery_life_min_hours") is not None:
                hard_conditions.append("p.battery_life_hours >= $battery_life_min_hours")

        where_clause = " AND ".join(hard_conditions)

        # ── Soft constraints (scoring) ────────────────────────────────────────
        # Use-case flags boost relevance but do NOT exclude products that lack them.
        # Weight 3 for primary use cases, 2 for secondary.
        #
        # Each good_for_* node property carries a float confidence (0.0–1.0)
        # produced by soft_tagger_v1. We threshold against $tag_threshold
        # (named Python constant TAG_CONFIDENCE_THRESHOLD in kg_projection)
        # rather than a Cypher literal so calibration (issue #60) is a
        # one-line change. coalesce(..., 0.0) handles products that never
        # received the tag — they score 0 instead of NULL-propagating.
        soft_score_cases: List[str] = []
        if filters:
            for flag, boost in [
                ("good_for_ml", 3),
                ("good_for_gaming", 3),
                ("good_for_web_dev", 2),
                ("good_for_creative", 2),
                ("good_for_linux", 2),
            ]:
                if filters.get(flag):
                    soft_score_cases.append(
                        f"CASE WHEN coalesce(p.{flag}, 0.0) >= $tag_threshold "
                        f"THEN {boost} ELSE 0 END"
                    )

        # Text match is also soft: preferred but not required.
        # Scoring is split into NAMED per-term buckets (soft, phrase, token,
        # connectivity) so the MerchantAgent can emit a full score_breakdown
        # on Offer.score and the shopping agent can attribute relevance.

        # Use-case flags (good_for_*) roll up into `soft_score`.
        if soft_score_cases:
            soft_expr = " + ".join(soft_score_cases)
        else:
            soft_expr = "0"

        # Phrase match: +3 if any of name/description/subcategory CONTAINS $q.
        if query and len(query) >= 2:
            phrase_expr = (
                "CASE WHEN (toLower(coalesce(p.subcategory, '')) CONTAINS $q OR "
                "toLower(coalesce(p.name, '')) CONTAINS $q OR "
                "toLower(coalesce(p.description, '')) CONTAINS $q) THEN 3 ELSE 0 END"
            )
        else:
            phrase_expr = "0"

        # Per-token match: +1 per token that hits name/description/subcategory.
        # Skip for single-token queries — phrase match already covers that.
        token_cases: List[str] = []
        if query and len(query) >= 2:
            tokens = self._tokenize_query(query)
            if len(tokens) >= 2:
                for i, _tok in enumerate(tokens):
                    pname = f"q_tok_{i}"
                    token_cases.append(
                        f"CASE WHEN (toLower(coalesce(p.subcategory, '')) CONTAINS ${pname} OR "
                        f"toLower(coalesce(p.name, '')) CONTAINS ${pname} OR "
                        f"toLower(coalesce(p.description, '')) CONTAINS ${pname}) THEN 1 ELSE 0 END"
                    )
        token_expr = " + ".join(token_cases) if token_cases else "0"

        has_scoring = bool(soft_score_cases or (query and len(query) >= 2))
        if has_scoring:
            cypher = f"""
            MATCH (p:Product)
            WHERE {where_clause}
            OPTIONAL MATCH (p)-[:SIMILAR_TO]->(nb:Product)
            WITH p,
                 count(nb) AS connectivity_raw,
                 toFloat({soft_expr}) AS soft_score,
                 toFloat({phrase_expr}) AS phrase_score,
                 toFloat({token_expr}) AS token_score
            WITH p, soft_score, phrase_score, token_score,
                 toFloat(connectivity_raw) * 0.1 AS connectivity_score
            ORDER BY (soft_score + phrase_score + token_score) DESC,
                     connectivity_score DESC, p.price ASC
            LIMIT $limit
            RETURN p.product_id AS product_id,
                   soft_score AS soft_score,
                   phrase_score AS phrase_score,
                   token_score AS token_score,
                   connectivity_score AS connectivity_score,
                   [p.name] AS path
            """
        else:
            # No soft constraints: rank by graph connectivity (centrality) then price
            cypher = f"""
            MATCH (p:Product)
            WHERE {where_clause}
            OPTIONAL MATCH (p)-[:SIMILAR_TO]->(nb:Product)
            WITH p, count(nb) AS connectivity_raw
            WITH p, toFloat(connectivity_raw) * 0.1 AS connectivity_score
            ORDER BY connectivity_score DESC, p.price ASC
            LIMIT $limit
            RETURN p.product_id AS product_id,
                   0.0 AS soft_score,
                   0.0 AS phrase_score,
                   0.0 AS token_score,
                   connectivity_score AS connectivity_score,
                   [p.name] AS path
            """
        return cypher
    
    def _extract_filters(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and normalize filters for Cypher query. KG stores price in dollars."""
        params = {}
        params["category"] = (filters or {}).get("category", "Electronics")
        f = filters or {}
        if f.get("brand") and str(f["brand"]).lower() not in ("no preference", "specific brand"):
            params["brand"] = f["brand"]
        if "subcategory" in f:
            params["subcategory"] = f["subcategory"]
        if "price_max_cents" in f:
            params["price_max"] = f["price_max_cents"] / 100.0
        elif "price_max" in f:
            params["price_max"] = float(f["price_max"])
        if "price_min_cents" in f:
            params["price_min"] = f["price_min_cents"] / 100.0
        elif "price_min" in f:
            params["price_min"] = float(f["price_min"])
        if f.get("good_for_ml"):
            params["good_for_ml"] = True
        if f.get("good_for_gaming"):
            params["good_for_gaming"] = True
        if f.get("good_for_web_dev"):
            params["good_for_web_dev"] = True
        if f.get("good_for_creative"):
            params["good_for_creative"] = True
        if f.get("good_for_linux"):
            params["good_for_linux"] = True
        if f.get("repairable"):
            params["repairable"] = True
        if f.get("refurbished"):
            params["refurbished"] = True
        if f.get("battery_life_min_hours") is not None:
            try:
                params["battery_life_min_hours"] = int(f["battery_life_min_hours"])
            except (TypeError, ValueError):
                pass
        return params
    
    def get_compatible_components(
        self,
        product_id: str,
        component_type: str = "all"
    ) -> List[Dict[str, Any]]:
        """
        Get compatible components for a product (e.g., compatible RAM for a laptop).
        
        Per week4notes.txt: KG handles component compatibility queries.
        
        Args:
            product_id: Product ID to find compatible components for
            component_type: Type of component (RAM, Storage, GPU, etc.) or "all"
        
        Returns:
            List of compatible component products with compatibility scores
        """
        if not self.is_available():
            return []
        
        try:
            with self.driver.session() as session:
                cypher = """
                MATCH (p:Product {product_id: $product_id})-[:COMPATIBLE_WITH]->(c:Component)
                """
                
                if component_type != "all":
                    cypher += " WHERE c.type = $component_type"
                
                cypher += """
                RETURN c.product_id AS product_id, 
                       c.name AS name,
                       c.type AS type,
                       c.price_cents AS price_cents
                ORDER BY c.price_cents ASC
                """
                
                result = session.run(cypher, {
                    "product_id": product_id,
                    "component_type": component_type
                })
                
                components = []
                for record in result:
                    components.append({
                        "product_id": record["product_id"],
                        "name": record["name"],
                        "type": record["type"],
                        "price_cents": record["price_cents"]
                    })
                
                return components
                
        except Exception as e:
            logger.error(f"Failed to get compatible components: {e}")
            return []
    
    def find_bundles(
        self,
        base_product_id: str,
        budget_max: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Find product bundles (e.g., gaming PC with compatible components).
        
        Per week4notes.txt: KG handles bundle queries for electronics.
        
        Args:
            base_product_id: Base product (e.g., laptop)
            budget_max: Maximum budget for bundle (in cents)
        
        Returns:
            List of compatible bundles with total price
        """
        if not self.is_available():
            return []
        
        try:
            with self.driver.session() as session:
                cypher = """
                MATCH (p:Product {product_id: $product_id})
                MATCH (p)-[:BUNDLED_WITH]->(b:Product)
                """
                
                if budget_max:
                    cypher += " WHERE (p.price_cents + b.price_cents) <= $budget_max"
                
                cypher += """
                RETURN b.product_id AS product_id,
                       b.name AS name,
                       b.price_cents AS price_cents,
                       (p.price_cents + b.price_cents) AS total_price
                ORDER BY total_price ASC
                LIMIT 10
                """
                
                result = session.run(cypher, {
                    "product_id": base_product_id,
                    "budget_max": budget_max
                })
                
                bundles = []
                for record in result:
                    bundles.append({
                        "product_id": record["product_id"],
                        "name": record["name"],
                        "price_cents": record["price_cents"],
                        "total_price": record["total_price"]
                    })
                
                return bundles
                
        except Exception as e:
            logger.error(f"Failed to find bundles: {e}")
            return []


    def get_similar_products(
        self,
        product_id: str,
        limit: int = 6,
    ) -> List[str]:
        """
        Return product_ids of products similar to the given product via
        SIMILAR_TO graph traversal (1 hop).  Falls back to [] if KG is
        unavailable so callers can fall back to SQL-based similarity.

        Graph schema: (p:Product)-[:SIMILAR_TO {score: float}]->(q:Product)
        Relationships are built by build_knowledge_graph.py / build_knowledge_graph_all.py.
        """
        if not self.is_available():
            return []
        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (p:Product {product_id: $pid})-[r:SIMILAR_TO]->(q:Product)
                    RETURN q.product_id AS product_id, coalesce(r.score, 1.0) AS score
                    ORDER BY score DESC
                    LIMIT $limit
                    """,
                    pid=product_id,
                    limit=limit,
                )
                ids = [rec["product_id"] for rec in result if rec.get("product_id")]
                logger.info(f"KG SIMILAR_TO found {len(ids)} neighbours for {product_id}")
                return ids
        except Exception as exc:
            logger.warning(f"KG get_similar_products failed: {exc}")
            return []

    def get_better_than(
        self,
        product_id: str,
        limit: int = 3,
    ) -> List[str]:
        """
        Return product_ids of products rated BETTER_THAN the given product
        (e.g. higher spec tier, better reviews).  Used in upgrade suggestions.
        Falls back to [] if KG unavailable.
        """
        if not self.is_available():
            return []
        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (q:Product)-[r:BETTER_THAN]->(p:Product {product_id: $pid})
                    RETURN q.product_id AS product_id, coalesce(r.score, 1.0) AS score
                    ORDER BY score DESC
                    LIMIT $limit
                    """,
                    pid=product_id,
                    limit=limit,
                )
                return [rec["product_id"] for rec in result if rec.get("product_id")]
        except Exception as exc:
            logger.warning(f"KG get_better_than failed: {exc}")
            return []

    def get_diverse_alternatives(
        self,
        product_ids: List[str],
        limit: int = 6,
    ) -> List[str]:
        """
        Return product_ids that are SIMILAR_TO any of the given products but
        NOT in the given set.  Ranked by connection count (degree) then avg
        similarity score — so highly-connected "central" products come first.

        Used to inject graph-diverse alternatives into the SQL result pool:
        these are products the SQL query didn't surface but which the KG knows
        are adjacent to what the user has already seen.

        Returns [] if KG is unavailable or the input set has no SIMILAR_TO edges.
        """
        if not self.is_available() or not product_ids:
            return []
        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    UNWIND $pids AS pid
                    MATCH (p:Product {product_id: pid})-[r:SIMILAR_TO]->(q:Product)
                    WHERE NOT q.product_id IN $pids
                    WITH q.product_id AS product_id,
                         avg(coalesce(r.score, 1.0)) AS avg_score,
                         count(r) AS degree
                    ORDER BY degree DESC, avg_score DESC
                    LIMIT $limit
                    RETURN product_id
                    """,
                    pids=product_ids,
                    limit=limit,
                )
                ids = [rec["product_id"] for rec in result if rec.get("product_id")]
                logger.info(
                    f"KG get_diverse_alternatives found {len(ids)} "
                    f"for {len(product_ids)} source products"
                )
                return ids
        except Exception as exc:
            logger.warning(f"KG get_diverse_alternatives failed: {exc}")
            return []


# Global instance
_kg_service: Optional[KnowledgeGraphService] = None


def get_kg_service() -> KnowledgeGraphService:
    """Get or create global KG service instance."""
    global _kg_service
    
    if _kg_service is None:
        import os
        neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD")  # No default — set in .env, do not commit
        _kg_service = KnowledgeGraphService(uri=neo4j_uri, user=neo4j_user, password=neo4j_password)
    
    return _kg_service
