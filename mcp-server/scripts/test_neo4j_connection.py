#!/usr/bin/env python3
"""
Test Neo4j Connection and Run Sample Queries

Quick test to verify Neo4j is running and accessible.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.neo4j_config import Neo4jConnection


def main():
    print("="*80)
    print("NEO4J CONNECTION TEST")
    print("="*80)
    
    print("\n1. Attempting to connect to Neo4j...")
    print("   URI: bolt://localhost:7687")
    print("   Username: neo4j")
    
    try:
        conn = Neo4jConnection()
        
        if conn.verify_connectivity():
            print(" Successfully connected to Neo4j!")
            
            # Run simple query
            print("\n2. Running test query...")
            result = conn.execute_query("RETURN 'Hello from Neo4j!' AS message")
            message = result.single()["message"]
            print(f" Query result: {message}")
            
            # Get node count
            print("\n3. Checking database contents...")
            result = conn.execute_query("MATCH (n) RETURN count(n) AS node_count")
            node_count = result.single()["node_count"]
            print(f" Total nodes in database: {node_count}")
            
            # Get relationship count
            result = conn.execute_query("MATCH ()-[r]->() RETURN count(r) AS rel_count")
            rel_count = result.single()["rel_count"]
            print(f" Total relationships: {rel_count}")
            
            if node_count == 0:
                print("\n Database is empty. Run build_knowledge_graph.py to populate it:")
                print("   python scripts/build_knowledge_graph.py")
            else:
                # Show sample data
                print("\n4. Sample data:")
                result = conn.execute_query("""
                    MATCH (n) 
                    RETURN labels(n)[0] AS type, count(n) AS count 
                    ORDER BY count DESC 
                    LIMIT 5
                """)
                for record in result:
                    print(f"   {record['type']}: {record['count']}")
            
            conn.close()
            
            print("\n" + "="*80)
            print(" NEO4J CONNECTION TEST PASSED")
            print("="*80)
            print("\nAccess Neo4j Browser at: http://localhost:7474")
            print("Username: neo4j")
            print("Password: password123")
            
        else:
            print("[FAIL] Failed to connect to Neo4j")
            print("\nTroubleshooting:")
            print("1. Check if Neo4j is running:")
            print("   docker ps | grep neo4j")
            print("2. Start Neo4j if needed:")
            print("   docker-compose -f docker-compose.neo4j.yml up -d")
            print("3. Check credentials in NEO4J_USERNAME and NEO4J_PASSWORD env vars")
            
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        print("\nMake sure Neo4j is running:")
        print("  docker-compose -f docker-compose.neo4j.yml up -d")


if __name__ == "__main__":
    main()
