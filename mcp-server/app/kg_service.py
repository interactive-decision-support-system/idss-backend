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
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
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
        limit: int = 20
    ) -> Tuple[List[str], Dict[str, Any]]:
        """
        Search for product candidates using knowledge graph.
        
        Per week4notes.txt: KG handles complex constraint-based queries.
        
        Args:
            query: Natural language query (e.g., "gaming laptop for video editing")
            filters: Structured filters (category, price_max, brand, etc.)
            limit: Maximum number of candidates to return
        
        Returns:
            Tuple of (product_ids, explanation_path)
            - product_ids: List of candidate product IDs from KG
            - explanation_path: Graph traversal explanation for debugging
        """
        if not self.is_available():
            return [], {}
        
        try:
            with self.driver.session() as session:
                cypher_query = self._build_cypher_query(query, filters, limit)
                params = {"limit": limit, **self._extract_filters(filters or {})}
                if query and len(query) >= 2:
                    params["q"] = query.lower()[:50]
                result = session.run(cypher_query, params)
                
                product_ids = []
                explanation_path = {
                    "query": query,
                    "filters": filters,
                    "traversal": []
                }
                
                for record in result:
                    product_id = record.get("product_id")
                    if product_id:
                        product_ids.append(product_id)
                        explanation_path["traversal"].append({
                            "product_id": product_id,
                            "score": record.get("score", 0.0),
                            "path": record.get("path", "")
                        })
                
                logger.info(f"KG search found {len(product_ids)} candidates for query: {query[:50]}")
                return product_ids, explanation_path
                
        except Exception as e:
            logger.error(f"KG search failed: {e}", exc_info=True)
            return [], {"error": str(e)}
    
    def _build_cypher_query(
        self,
        query: str,
        filters: Optional[Dict[str, Any]],
        limit: int
    ) -> str:
        """
        Build Cypher query for product search.
        
        Handles:
        - Use case matching (gaming, video editing, work)
        - Component compatibility (CPU-GPU-RAM)
        - Price-performance relationships
        - Brand/product line relationships
        """
        # Match our KG schema: Product nodes with product_id, name, brand, price (dollars), category, subcategory
        # Supports all categories: Electronics, Books, Jewelry, Accessories, etc.
        category = (filters or {}).get("category", "Electronics")
        conditions = ["p.category = $category"]

        if filters:
            if filters.get("brand") and str(filters["brand"]).lower() not in ("no preference", "specific brand"):
                conditions.append("p.brand = $brand")
            if filters.get("subcategory"):
                conditions.append("p.subcategory = $subcategory")
            if filters.get("price_max") is not None or filters.get("price_max_cents") is not None:
                conditions.append("p.price <= $price_max")
            if filters.get("price_min") is not None or filters.get("price_min_cents") is not None:
                conditions.append("p.price >= $price_min")

        where_clause = " AND ".join(conditions)
        # Optional text search: match query terms in name/subcategory/description
        if query and len(query) >= 2:
            conditions.append(
                "(toLower(coalesce(p.subcategory, '')) CONTAINS $q OR "
                "toLower(coalesce(p.name, '')) CONTAINS $q OR "
                "toLower(coalesce(p.description, '')) CONTAINS $q)"
            )
            where_clause = " AND ".join(conditions)

        cypher = f"""
        MATCH (p:Product)
        WHERE {where_clause}
        WITH p
        ORDER BY p.price ASC
        LIMIT $limit
        RETURN p.product_id AS product_id, 1.0 AS score, [p.name] AS path
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
