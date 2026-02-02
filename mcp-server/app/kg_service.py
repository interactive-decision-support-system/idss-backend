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
                # Build Cypher query based on filters
                cypher_query = self._build_cypher_query(query, filters, limit)
                
                result = session.run(cypher_query, {
                    "query": query,
                    "limit": limit,
                    **self._extract_filters(filters or {})
                })
                
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
        # Base query: Match products with relationships
        cypher = """
        MATCH (p:Product)
        WHERE p.category = 'Electronics'
        """
        
        # Add filters
        conditions = []
        params = {}
        
        if filters:
            if "category" in filters:
                conditions.append("p.category = $category")
                params["category"] = filters["category"]
            
            if "brand" in filters:
                conditions.append("p.brand = $brand")
                params["brand"] = filters["brand"]
            
            if "price_max" in filters:
                conditions.append("p.price_cents <= $price_max")
                params["price_max"] = int(filters["price_max"] * 100)  # Convert to cents
            
            if "price_min" in filters:
                conditions.append("p.price_cents >= $price_min")
                params["price_min"] = int(filters["price_min"] * 100)
        
        # Use case matching (from query text)
        use_cases = []
        query_lower = query.lower()
        
        if "gaming" in query_lower:
            use_cases.append("Gaming")
        if "video editing" in query_lower or "video" in query_lower:
            use_cases.append("VideoEditing")
        if "work" in query_lower or "business" in query_lower:
            use_cases.append("Work")
        if "school" in query_lower or "student" in query_lower:
            use_cases.append("School")
        
        if use_cases:
            # Match products with USE_CASE relationships
            cypher = """
            MATCH (p:Product)-[:SUITABLE_FOR]->(uc:UseCase)
            WHERE p.category = 'Electronics'
            AND uc.name IN $use_cases
            """
            params["use_cases"] = use_cases
        
        # Add WHERE conditions
        if conditions:
            cypher += " AND " + " AND ".join(conditions)
        
        # Score by relevance (use case match, price-performance, etc.)
        cypher += """
        WITH p, 
             CASE 
               WHEN EXISTS((p)-[:SUITABLE_FOR]->(:UseCase)) THEN 1.0
               ELSE 0.5
             END AS relevance_score
        ORDER BY relevance_score DESC, p.price_cents ASC
        LIMIT $limit
        RETURN p.product_id AS product_id, relevance_score AS score, 
               [p.name] AS path
        """
        
        return cypher
    
    def _extract_filters(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and normalize filters for Cypher query."""
        params = {}
        
        if "category" in filters:
            params["category"] = filters["category"]
        if "brand" in filters:
            params["brand"] = filters["brand"]
        if "price_max" in filters:
            params["price_max"] = int(filters["price_max"] * 100)
        if "price_min" in filters:
            params["price_min"] = int(filters["price_min"] * 100)
        
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
