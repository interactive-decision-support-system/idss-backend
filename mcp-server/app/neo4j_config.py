"""
Neo4j Knowledge Graph Configuration

Connection and configuration for Neo4j graph database.
"""

import os
from neo4j import GraphDatabase
from typing import Optional


class Neo4jConnection:
    """Manages Neo4j database connection."""
    
    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: str = "neo4j"
    ):
        """
        Initialize Neo4j connection.
        
        Args:
            uri: Neo4j URI (default: env NEO4J_URI or bolt://localhost:7687)
            username: Neo4j username (default: env NEO4J_USERNAME or neo4j)
            password: Neo4j password (default: env NEO4J_PASSWORD)
            database: Database name (default: neo4j)
        """
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = username or os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.database = database
        
        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.username, self.password)
        )
    
    def close(self):
        """Close the driver connection."""
        if self.driver:
            self.driver.close()
    
    def verify_connectivity(self):
        """Verify connection to Neo4j."""
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run("RETURN 1 AS num")
                return result.single()["num"] == 1
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def execute_query(self, query: str, parameters: dict = None):
        """
        Execute a Cypher query.
        
        Args:
            query: Cypher query string
            parameters: Query parameters
            
        Returns:
            Query result
        """
        with self.driver.session(database=self.database) as session:
            return session.run(query, parameters or {})
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Default connection instance
_default_connection: Optional[Neo4jConnection] = None


def get_connection() -> Neo4jConnection:
    """Get or create default Neo4j connection."""
    global _default_connection
    if _default_connection is None:
        _default_connection = Neo4jConnection()
    return _default_connection


def close_connection():
    """Close default Neo4j connection."""
    global _default_connection
    if _default_connection:
        _default_connection.close()
        _default_connection = None
