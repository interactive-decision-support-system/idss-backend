#!/bin/bash
# Automated scraper for real-only database (laptops, phones, books).
# Run on a schedule (cron) to refresh product data.
#
# Setup cron (daily at 3am):
#   crontab -e
#   add: 0 3 * * * /path/to/idss-backend/mcp-server/scripts/run_scheduled_scrape.sh >> /var/log/idss-scrape.log 2>&1
#
# Or run manually:
#   ./scripts/run_scheduled_scrape.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$MCP_DIR"

echo "[$(date)] Starting scheduled scrape..."

# Load env if present
if [ -f "$MCP_DIR/../.env" ]; then
  set -a
  source "$MCP_DIR/../.env"
  set +a
fi

# 1. Populate real-only DB (--full = books + Selenium for blocked sites)
python scripts/populate_real_only_db.py --full

# 2. Backfill kg_features
python scripts/backfill_kg_features.py

# 3. Rebuild Neo4j knowledge graph (optional; comment out if not using Neo4j)
if command -v python &>/dev/null; then
  python scripts/build_knowledge_graph.py --clear 2>/dev/null || echo "[WARN] Neo4j build skipped (Neo4j may not be running)"
fi

echo "[$(date)] Scheduled scrape complete"
