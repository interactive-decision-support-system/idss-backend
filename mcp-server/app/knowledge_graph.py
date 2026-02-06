"""
Complex Neo4j Knowledge Graph for E-Commerce

Per kg.txt and MemOS/Zep/OpenClaw research:
- Product graph: laptops, books, jewelry, accessories, all categories
- Session intent: Explore | Decide today | Execute purchase (big goal)
- Step intent: Research | Compare | Negotiate | Schedule | Return (next action)
- Session memory: track important info across session for next meeting

Builds a rich, multi-dimensional knowledge graph with:
- Detailed component relationships
- Manufacturing and supply chain data
- User interactions and reviews
- Software compatibility
- Literary connections
- Genre hierarchies
- Session/user memory (MemOS-style graph memory)
"""

from typing import Dict, Any, List, Optional
from neo4j import GraphDatabase
from app.neo4j_config import Neo4jConnection
import json


class KnowledgeGraphBuilder:
    """Builds and manages complex knowledge graph."""
    
    def __init__(self, connection: Neo4jConnection):
        self.conn = connection
        self.driver = connection.driver
        self.database = connection.database
    
    def create_indexes_and_constraints(self):
        """Create indexes and constraints for performance."""
        queries = [
            # Constraints (ensure uniqueness)
            "CREATE CONSTRAINT product_id IF NOT EXISTS FOR (p:Product) REQUIRE p.product_id IS UNIQUE",
            "CREATE CONSTRAINT laptop_id IF NOT EXISTS FOR (l:Laptop) REQUIRE l.product_id IS UNIQUE",
            "CREATE CONSTRAINT book_id IF NOT EXISTS FOR (b:Book) REQUIRE b.product_id IS UNIQUE",
            "CREATE CONSTRAINT jewelry_id IF NOT EXISTS FOR (j:Jewelry) REQUIRE j.product_id IS UNIQUE",
            "CREATE CONSTRAINT accessory_id IF NOT EXISTS FOR (a:Accessory) REQUIRE a.product_id IS UNIQUE",
            "CREATE CONSTRAINT author_name IF NOT EXISTS FOR (a:Author) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT manufacturer_name IF NOT EXISTS FOR (m:Manufacturer) REQUIRE m.name IS UNIQUE",
            "CREATE CONSTRAINT cpu_model IF NOT EXISTS FOR (c:CPU) REQUIRE c.model IS UNIQUE",
            "CREATE CONSTRAINT gpu_model IF NOT EXISTS FOR (g:GPU) REQUIRE g.model IS UNIQUE",
            "CREATE CONSTRAINT genre_name IF NOT EXISTS FOR (g:Genre) REQUIRE g.name IS UNIQUE",
            "CREATE CONSTRAINT publisher_name IF NOT EXISTS FOR (p:Publisher) REQUIRE p.name IS UNIQUE",
            
            # Indexes (for fast lookups)
            "CREATE INDEX product_name IF NOT EXISTS FOR (p:Product) ON (p.name)",
            "CREATE INDEX product_price IF NOT EXISTS FOR (p:Product) ON (p.price)",
            "CREATE INDEX product_brand IF NOT EXISTS FOR (p:Product) ON (p.brand)",
            "CREATE INDEX book_title IF NOT EXISTS FOR (b:Book) ON (b.title)",
            "CREATE INDEX laptop_model IF NOT EXISTS FOR (l:Laptop) ON (l.model)",
            "CREATE INDEX review_rating IF NOT EXISTS FOR (r:Review) ON (r.rating)",
            "CREATE INDEX user_id IF NOT EXISTS FOR (u:User) ON (u.user_id)",
            "CREATE INDEX jewelry_brand IF NOT EXISTS FOR (j:Jewelry) ON (j.brand)",
            "CREATE INDEX accessory_brand IF NOT EXISTS FOR (a:Accessory) ON (a.brand)",
            "CREATE INDEX product_category IF NOT EXISTS FOR (p:Product) ON (p.category)",
            "CREATE INDEX user_session_id IF NOT EXISTS FOR (s:UserSession) ON (s.session_id)",
            "CREATE INDEX session_intent_name IF NOT EXISTS FOR (si:SessionIntent) ON (si.name)",
            "CREATE INDEX step_intent_name IF NOT EXISTS FOR (st:StepIntent) ON (st.name)",
        ]
        
        for query in queries:
            try:
                with self.driver.session(database=self.database) as session:
                    session.run(query)
                print(f"OK {query.split()[1]}: {query.split()[2] if len(query.split()) > 2 else ''}")
            except Exception as e:
                print(f"[WARN] {query}: {e}")
    
    def create_laptop_node(self, laptop_data: Dict[str, Any]) -> str:
        """
        Create a complex laptop node with all components and relationships.
        
        Args:
            laptop_data: Dictionary containing laptop information
            
        Returns:
            Product ID of created laptop
        """
        query = """
        // Create main Laptop node
        MERGE (l:Laptop:Product {product_id: $product_id})
        SET l.name = $name,
            l.brand = $brand,
            l.model = $model,
            l.price = $price,
            l.description = $description,
            l.image_url = $image_url,
            l.category = 'Electronics',
            l.subcategory = $subcategory,
            l.available = $available,
            l.weight_kg = $weight_kg,
            l.portability_score = $portability_score,
            l.battery_life_hours = $battery_life_hours,
            l.screen_size_inches = $screen_size_inches,
            l.refresh_rate_hz = $refresh_rate_hz,
            l.created_at = datetime()
        
        // Create or connect Manufacturer
        MERGE (m:Manufacturer {name: $brand})
        SET m.country = $manufacturer_country,
            m.founded_year = $manufacturer_founded,
            m.website = $manufacturer_website
        MERGE (l)-[:MANUFACTURED_BY]->(m)
        
        // Create CPU node
        MERGE (cpu:CPU {model: $cpu_model})
        SET cpu.manufacturer = $cpu_manufacturer,
            cpu.cores = $cpu_cores,
            cpu.threads = $cpu_threads,
            cpu.base_clock_ghz = $cpu_base_clock,
            cpu.boost_clock_ghz = $cpu_boost_clock,
            cpu.tdp_watts = $cpu_tdp,
            cpu.generation = $cpu_generation,
            cpu.tier = $cpu_tier
        MERGE (l)-[:HAS_CPU]->(cpu)
        
        // Create GPU node (if exists)
        FOREACH (ignore IN CASE WHEN $gpu_model IS NOT NULL THEN [1] ELSE [] END |
            MERGE (gpu:GPU {model: $gpu_model})
            SET gpu.manufacturer = $gpu_manufacturer,
                gpu.vram_gb = $gpu_vram,
                gpu.memory_type = $gpu_memory_type,
                gpu.tdp_watts = $gpu_tdp,
                gpu.tier = $gpu_tier,
                gpu.ray_tracing = $gpu_ray_tracing
            MERGE (l)-[:HAS_GPU]->(gpu)
        )
        
        // Create RAM node
        MERGE (ram:RAM {capacity_gb: $ram_capacity, type: $ram_type})
        SET ram.speed_mhz = $ram_speed,
            ram.channels = $ram_channels,
            ram.expandable = $ram_expandable
        MERGE (l)-[:HAS_RAM]->(ram)
        
        // Create Storage node
        MERGE (storage:Storage {capacity_gb: $storage_capacity, type: $storage_type})
        SET storage.interface = $storage_interface,
            storage.read_speed_mbps = $storage_read_speed,
            storage.write_speed_mbps = $storage_write_speed,
            storage.expandable = $storage_expandable
        MERGE (l)-[:HAS_STORAGE]->(storage)
        
        // Create Display node
        MERGE (display:Display {size_inches: $screen_size_inches, resolution: $display_resolution})
        SET display.panel_type = $display_panel_type,
            display.refresh_rate_hz = $refresh_rate_hz,
            display.brightness_nits = $display_brightness,
            display.color_gamut = $display_color_gamut,
            display.touch_screen = $display_touch
        MERGE (l)-[:HAS_DISPLAY]->(display)
        
        RETURN l.product_id AS product_id
        """
        
        with self.driver.session(database=self.database) as session:
            result = session.run(query, laptop_data)
            return result.single()["product_id"]
    
    def create_book_node(self, book_data: Dict[str, Any]) -> str:
        """
        Create a complex book node with author, genre, publisher relationships.
        
        Args:
            book_data: Dictionary containing book information
            
        Returns:
            Product ID of created book
        """
        query = """
        // Create main Book node
        MERGE (b:Book:Product {product_id: $product_id})
        SET b.title = $title,
            b.name = $name,
            b.price = $price,
            b.description = $description,
            b.image_url = $image_url,
            b.category = 'Books',
            b.isbn = $isbn,
            b.pages = $pages,
            b.language = $language,
            b.publication_year = $publication_year,
            b.edition = $edition,
            b.format = $format,
            b.available = $available,
            b.created_at = datetime()
        
        // Create or connect Author
        MERGE (a:Author {name: $author})
        SET a.nationality = $author_nationality,
            a.birth_year = $author_birth_year,
            a.biography = $author_biography,
            a.awards = $author_awards
        MERGE (b)-[:WRITTEN_BY]->(a)
        
        // Create or connect Publisher
        MERGE (p:Publisher {name: $publisher})
        SET p.country = $publisher_country,
            p.founded_year = $publisher_founded,
            p.website = $publisher_website
        MERGE (b)-[:PUBLISHED_BY {year: $publication_year}]->(p)
        
        // Connect to primary Genre
        MERGE (g:Genre {name: $genre})
        SET g.description = $genre_description
        MERGE (b)-[:BELONGS_TO_GENRE]->(g)
        
        // Connect to Themes (multiple)
        FOREACH (theme IN $themes |
            MERGE (t:Theme {name: theme})
            MERGE (b)-[:EXPLORES_THEME]->(t)
        )
        
        // Create Series relationship (only when series_name AND series_position are set)
        FOREACH (ignore IN CASE WHEN $series_name IS NOT NULL AND $series_position IS NOT NULL THEN [1] ELSE [] END |
            MERGE (s:Series {name: $series_name})
            SET s.total_books = $series_total_books
            MERGE (b)-[:PART_OF_SERIES {position: $series_position}]->(s)
        )
        
        RETURN b.product_id AS product_id
        """
        
        with self.driver.session(database=self.database) as session:
            result = session.run(query, book_data)
            return result.single()["product_id"]
    
    def create_jewelry_node(self, jewelry_data: Dict[str, Any]) -> str:
        """
        Create a jewelry node with brand, material, and item type relationships.
        
        Args:
            jewelry_data: Dictionary containing jewelry information
            
        Returns:
            Product ID of created jewelry
        """
        query = """
        // Create main Jewelry node
        MERGE (j:Jewelry:Product {product_id: $product_id})
        SET j.name = $name,
            j.brand = $brand,
            j.price = $price,
            j.description = $description,
            j.image_url = $image_url,
            j.category = 'Jewelry',
            j.subcategory = $subcategory,
            j.color = $color,
            j.available = $available,
            j.created_at = datetime()
        
        // Create or connect Brand
        FOREACH (ignore IN CASE WHEN $brand IS NOT NULL AND $brand <> '' THEN [1] ELSE [] END |
            MERGE (b:Brand {name: $brand})
            MERGE (j)-[:BRANDED_BY]->(b)
        )
        
        // Create or connect Material
        FOREACH (ignore IN CASE WHEN $material IS NOT NULL AND $material <> '' THEN [1] ELSE [] END |
            MERGE (m:Material {name: $material})
            MERGE (j)-[:MADE_OF]->(m)
        )
        
        // Create or connect ItemType
        FOREACH (ignore IN CASE WHEN $item_type IS NOT NULL AND $item_type <> '' THEN [1] ELSE [] END |
            MERGE (it:ItemType {name: $item_type})
            MERGE (j)-[:IS_TYPE]->(it)
        )
        
        RETURN j.product_id AS product_id
        """
        
        with self.driver.session(database=self.database) as session:
            result = session.run(query, jewelry_data)
            return result.single()["product_id"]
    
    def create_accessory_node(self, accessory_data: Dict[str, Any]) -> str:
        """
        Create an accessory node with brand and item type relationships.
        
        Args:
            accessory_data: Dictionary containing accessory information
            
        Returns:
            Product ID of created accessory
        """
        query = """
        // Create main Accessory node
        MERGE (a:Accessory:Product {product_id: $product_id})
        SET a.name = $name,
            a.brand = $brand,
            a.price = $price,
            a.description = $description,
            a.image_url = $image_url,
            a.category = 'Accessories',
            a.subcategory = $subcategory,
            a.color = $color,
            a.available = $available,
            a.created_at = datetime()
        
        // Create or connect Brand
        FOREACH (ignore IN CASE WHEN $brand IS NOT NULL AND $brand <> '' THEN [1] ELSE [] END |
            MERGE (b:Brand {name: $brand})
            MERGE (a)-[:BRANDED_BY]->(b)
        )
        
        // Create or connect ItemType
        FOREACH (ignore IN CASE WHEN $item_type IS NOT NULL AND $item_type <> '' THEN [1] ELSE [] END |
            MERGE (it:ItemType {name: $item_type})
            MERGE (a)-[:IS_TYPE]->(it)
        )
        
        RETURN a.product_id AS product_id
        """
        
        with self.driver.session(database=self.database) as session:
            result = session.run(query, accessory_data)
            return result.single()["product_id"]
    
    def create_generic_product_node(self, product_data: Dict[str, Any]) -> str:
        """
        Create a generic Product node for Beauty, Clothing, Art, Food, etc.
        
        Args:
            product_data: Dictionary containing product information
            
        Returns:
            Product ID of created product
        """
        query = """
        // Create main Product node (generic; includes source/scraped_from_url for provenance)
        MERGE (p:Product {product_id: $product_id})
        SET p.name = $name,
            p.brand = $brand,
            p.price = $price,
            p.description = $description,
            p.image_url = $image_url,
            p.category = $category,
            p.subcategory = $subcategory,
            p.product_type = $product_type,
            p.color = $color,
            p.available = $available,
            p.source = $source,
            p.scraped_from_url = $scraped_from_url,
            p.created_at = datetime()
        
        // Create or connect Brand
        FOREACH (ignore IN CASE WHEN $brand IS NOT NULL AND $brand <> '' THEN [1] ELSE [] END |
            MERGE (b:Brand {name: $brand})
            MERGE (p)-[:BRANDED_BY]->(b)
        )
        
        // Create or connect Category node
        MERGE (c:Category {name: $category})
        MERGE (p)-[:IN_CATEGORY]->(c)
        
        RETURN p.product_id AS product_id
        """
        
        with self.driver.session(database=self.database) as session:
            result = session.run(query, product_data)
            return result.single()["product_id"]
    
    def create_session_memory(
        self,
        session_id: str,
        user_id: Optional[str],
        session_intent: str,
        step_intent: str,
        important_info: Dict[str, Any],
    ) -> None:
        """
        Create session memory node per kg.txt: track session intent, step intent,
        and important info for next meeting. Inspired by MemOS (graph memory) and Zep (temporal context).
        
        Session intent: Explore | Decide today | Execute purchase
        Step intent: Research | Compare | Negotiate | Schedule | Return/post-purchase
        """
        import json
        query = """
        MERGE (u:User {user_id: $user_id})
        SET u.last_seen = datetime()
        
        MERGE (s:UserSession {session_id: $session_id})
        SET s.updated_at = datetime(),
            s.session_intent = $session_intent,
            s.step_intent = $step_intent,
            s.important_info = $important_info_json
        
        WITH s
        MERGE (si:SessionIntent {name: $session_intent})
        MERGE (s)-[:HAS_SESSION_INTENT]->(si)
        
        WITH s
        MERGE (st:StepIntent {name: $step_intent})
        MERGE (s)-[:HAS_STEP_INTENT]->(st)
        
        WITH s, u
        MERGE (u)-[:HAS_SESSION]->(s)
        """
        with self.driver.session(database=self.database) as session:
            session.run(query, {
                "session_id": session_id,
                "user_id": user_id or "anonymous",
                "session_intent": session_intent or "Explore",
                "step_intent": step_intent or "Research",
                "important_info_json": json.dumps(important_info),
            })
    
    def update_step_intent(self, session_id: str, step_intent: str) -> None:
        """Update step intent for a session (next action: Research, Compare, etc.)."""
        query = """
        MATCH (s:UserSession {session_id: $session_id})
        SET s.step_intent = $step_intent,
            s.updated_at = datetime()
        WITH s
        MERGE (st:StepIntent {name: $step_intent})
        MERGE (s)-[:HAS_STEP_INTENT]->(st)
        """
        with self.driver.session(database=self.database) as session:
            session.run(query, {"session_id": session_id, "step_intent": step_intent})
    
    def get_session_memory(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session memory for context (MemOS-style recall before agent)."""
        query = """
        MATCH (s:UserSession {session_id: $session_id})
        OPTIONAL MATCH (s)-[:HAS_SESSION_INTENT]->(si:SessionIntent)
        OPTIONAL MATCH (s)-[:HAS_STEP_INTENT]->(st:StepIntent)
        RETURN s.session_intent AS session_intent,
               s.step_intent AS step_intent,
               s.important_info AS important_info
        """
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(query, {"session_id": session_id})
                record = result.single()
                if not record:
                    return None
                import json
                info = record.get("important_info")
                if isinstance(info, str):
                    try:
                        info = json.loads(info)
                    except Exception:
                        info = {}
                return {
                    "session_intent": record.get("session_intent"),
                    "step_intent": record.get("step_intent"),
                    "important_info": info or {},
                }
        except Exception:
            return None
    
    def create_review_relationships(self, review_data: Dict[str, Any]):
        """
        Create review nodes and relationships with sentiment analysis.
        
        Args:
            review_data: Dictionary containing review information
        """
        query = """
        MATCH (p:Product {product_id: $product_id})
        
        MERGE (u:User {user_id: $user_id})
        SET u.username = $username,
            u.join_date = $user_join_date,
            u.verified = $user_verified
        
        CREATE (r:Review {review_id: $review_id})
        SET r.rating = $rating,
            r.comment = $comment,
            r.sentiment_score = $sentiment_score,
            r.sentiment_label = $sentiment_label,
            r.helpful_count = $helpful_count,
            r.verified_purchase = $verified_purchase,
            r.created_at = datetime($review_date)
        
        MERGE (u)-[:WROTE_REVIEW]->(r)
        MERGE (r)-[:REVIEWS]->(p)
        
        // Add sentiment node for analysis
        MERGE (s:Sentiment {label: $sentiment_label})
        MERGE (r)-[:HAS_SENTIMENT]->(s)
        """
        
        with self.driver.session(database=self.database) as session:
            session.run(query, review_data)
    
    def create_software_compatibility(self, laptop_id: str, software_list: List[Dict[str, Any]]):
        """
        Create software compatibility relationships for laptops.
        
        Args:
            laptop_id: Laptop product ID
            software_list: List of compatible software
        """
        query = """
        MATCH (l:Laptop {product_id: $laptop_id})
        
        UNWIND $software_list AS sw
        MERGE (s:Software {name: sw.name})
        SET s.category = sw.category,
            s.version = sw.version,
            s.developer = sw.developer,
            s.license_type = sw.license_type
        
        MERGE (l)-[c:COMPATIBLE_WITH]->(s)
        SET c.performance_rating = sw.performance_rating,
            c.tested_date = datetime()
        
        // Create Operating System relationship
        MERGE (os:OperatingSystem {name: sw.os_name})
        SET os.version = sw.os_version,
            os.architecture = sw.os_architecture
        MERGE (l)-[:RUNS]->(os)
        """
        
        with self.driver.session(database=self.database) as session:
            session.run(query, {"laptop_id": laptop_id, "software_list": software_list})
    
    def create_comparison_relationships(self, product_id1: str, product_id2: str, comparison_type: str, score: float):
        """
        Create comparison relationships between products.
        
        Args:
            product_id1: First product ID
            product_id2: Second product ID
            comparison_type: Type of comparison (SIMILAR_TO, BETTER_THAN, CHEAPER_THAN, etc.)
            score: Similarity/comparison score (0-1)
        """
        query = f"""
        MATCH (p1:Product {{product_id: $product_id1}})
        MATCH (p2:Product {{product_id: $product_id2}})
        
        MERGE (p1)-[r:{comparison_type}]->(p2)
        SET r.score = $score,
            r.calculated_at = datetime()
        """
        
        with self.driver.session(database=self.database) as session:
            session.run(query, {
                "product_id1": product_id1,
                "product_id2": product_id2,
                "score": score
            })
    
    def create_genre_hierarchy(self, genre_data: List[Dict[str, Any]]):
        """
        Create hierarchical genre relationships.
        
        Args:
            genre_data: List of genre hierarchy data
        """
        query = """
        UNWIND $genre_data AS g
        
        MERGE (genre:Genre {name: g.name})
        SET genre.description = g.description,
            genre.level = g.level
        
        FOREACH (parent IN g.parent_genres |
            MERGE (parent_genre:Genre {name: parent})
            MERGE (genre)-[:SUBGENRE_OF]->(parent_genre)
        )
        """
        
        with self.driver.session(database=self.database) as session:
            session.run(query, {"genre_data": genre_data})
    
    def create_literary_connections(self, book_id1: str, book_id2: str, connection_type: str, description: str = ""):
        """
        Create literary connections between books.
        
        Args:
            book_id1: First book ID
            book_id2: Second book ID
            connection_type: Type (INSPIRED_BY, SEQUEL_TO, SIMILAR_THEME, etc.)
            description: Optional description
        """
        query = f"""
        MATCH (b1:Book {{product_id: $book_id1}})
        MATCH (b2:Book {{product_id: $book_id2}})
        
        MERGE (b1)-[r:{connection_type}]->(b2)
        SET r.description = $description,
            r.created_at = datetime()
        """
        
        with self.driver.session(database=self.database) as session:
            session.run(query, {
                "book_id1": book_id1,
                "book_id2": book_id2,
                "description": description
            })
    
    def create_user_interactions(self, user_id: str, product_id: str, interaction_data: Dict[str, Any]):
        """
        Create user interaction relationships (purchases, wishlists, views).
        
        Args:
            user_id: User ID
            product_id: Product ID
            interaction_data: Interaction details
        """
        interaction_type = interaction_data.get("type", "VIEWED")
        
        query = f"""
        MERGE (u:User {{user_id: $user_id}})
        MATCH (p:Product {{product_id: $product_id}})
        
        MERGE (u)-[i:{interaction_type}]->(p)
        SET i.timestamp = datetime($timestamp),
            i.session_id = $session_id,
            i.duration_seconds = $duration_seconds,
            i.price_at_time = $price_at_time
        """
        
        params = {
            "user_id": user_id,
            "product_id": product_id,
            "timestamp": interaction_data.get("timestamp"),
            "session_id": interaction_data.get("session_id"),
            "duration_seconds": interaction_data.get("duration_seconds", 0),
            "price_at_time": interaction_data.get("price_at_time")
        }
        
        with self.driver.session(database=self.database) as session:
            session.run(query, params)
    
    def create_supply_chain_relationships(self, manufacturer_data: Dict[str, Any]):
        """
        Create supply chain relationships for manufacturers.
        
        Args:
            manufacturer_data: Manufacturer and supplier data
        """
        query = """
        MERGE (m:Manufacturer {name: $manufacturer_name})
        
        UNWIND $suppliers AS sup
        MERGE (s:Supplier {name: sup.name})
        SET s.country = sup.country,
            s.specialization = sup.specialization,
            s.rating = sup.rating
        
        MERGE (m)-[r:SOURCES_FROM]->(s)
        SET r.component_type = sup.component_type,
            r.reliability_score = sup.reliability_score,
            r.lead_time_days = sup.lead_time_days
        
        UNWIND $factories AS fac
        MERGE (f:Factory {location: fac.location})
        SET f.capacity = fac.capacity,
            f.established_year = fac.established_year
        
        MERGE (m)-[:OPERATES]->(f)
        """
        
        with self.driver.session(database=self.database) as session:
            session.run(query, manufacturer_data)
    
    def clear_all_data(self):
        """Clear all data from the graph (use with caution!)."""
        query = "MATCH (n) DETACH DELETE n"
        with self.driver.session(database=self.database) as session:
            session.run(query)
        print("[WARN] All graph data cleared")
    
    def get_graph_statistics(self) -> Dict[str, Any]:
        """Get statistics about the knowledge graph."""
        queries = {
            "total_nodes": "MATCH (n) RETURN count(n) AS count",
            "total_relationships": "MATCH ()-[r]->() RETURN count(r) AS count",
            "node_types": "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC",
            "relationship_types": "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC",
            "laptops": "MATCH (l:Laptop) RETURN count(l) AS count",
            "books": "MATCH (b:Book) RETURN count(b) AS count",
            "jewelry": "MATCH (j:Jewelry) RETURN count(j) AS count",
            "accessories": "MATCH (a:Accessory) RETURN count(a) AS count",
            "authors": "MATCH (a:Author) RETURN count(a) AS count",
            "reviews": "MATCH (r:Review) RETURN count(r) AS count",
            "users": "MATCH (u:User) RETURN count(u) AS count",
        }
        
        stats = {}
        with self.driver.session(database=self.database) as session:
            for key, query in queries.items():
                try:
                    result = session.run(query)
                    if key in ["node_types", "relationship_types"]:
                        stats[key] = [dict(record) for record in result]
                    else:
                        stats[key] = result.single()["count"]
                except Exception as e:
                    stats[key] = f"Error: {e}"
        
        return stats
