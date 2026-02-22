#!/bin/bash
echo "  Starting Neo4j with Docker..."
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo " Docker is not running. Please start Docker Desktop first."
    exit 1
fi

# Start Neo4j
docker-compose -f docker-compose-neo4j.yml up -d

echo ""
echo "⏳ Waiting for Neo4j to start (30 seconds)..."
sleep 30

echo ""
echo " Neo4j should now be running!"
echo ""
echo " Connection Details:"
echo "   URI: bolt://localhost:7687"
echo "   Username: neo4j"
echo "   Password: neo4jpassword"
echo "   Browser: http://localhost:7474"
echo ""
echo " Next step: Populate the database"
echo "   python mcp-server/scripts/populate_all_databases.py"
echo ""
