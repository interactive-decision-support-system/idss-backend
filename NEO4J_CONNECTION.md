# Neo4j Connection (Port 7688)

Because port 7687 was already in use, Neo4j runs on **alternate ports**:

| Service | Port | URL |
|---------|------|-----|
| **Bolt** (API, Python, build script) | **7688** | `bolt://localhost:7688` |
| **Browser** (HTTP UI) | **7475** | http://localhost:7475 |

## Neo4j Browser

1. Open **http://localhost:7475** (not 7474)
2. Click **Connect** or **:server connect**
3. Use:
   - **Connect URL:** `neo4j://localhost:7688` or `bolt://localhost:7688`
   - **Username:** `neo4j`
   - **Password:** `neo4jpassword`

If you see "Unauthorized", you're likely connecting to the wrong port (7687 = old/other instance; 7688 = our container).
