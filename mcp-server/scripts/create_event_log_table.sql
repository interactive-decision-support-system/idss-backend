-- Create append-only event log table for MCP
-- This table stores all MCP requests/responses for research replay and debugging

CREATE TABLE IF NOT EXISTS mcp_events (
    -- Primary key
    event_id BIGSERIAL PRIMARY KEY,
    
    -- Timestamp (when event occurred)
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Request identification
    request_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255),
    
    -- Tool/endpoint information
    tool_name VARCHAR(100) NOT NULL,  -- e.g., 'search_products', 'get_product', 'add_to_cart', 'checkout'
    endpoint_path VARCHAR(255),  -- e.g., '/api/search-products'
    
    -- Input (redacted for privacy)
    input_hash VARCHAR(64),  -- SHA-256 hash of input for deduplication
    input_summary JSONB,  -- Redacted summary of input (no sensitive data)
    
    -- Outcome
    outcome_status VARCHAR(50) NOT NULL,  -- 'OK', 'OUT_OF_STOCK', 'NOT_FOUND', 'INVALID', 'ERROR'
    constraints_count INTEGER DEFAULT 0,  -- Number of constraints returned
    
    -- Performance metrics
    latency_ms NUMERIC(10, 2),  -- Total latency in milliseconds
    cache_hit BOOLEAN DEFAULT FALSE,
    timings_breakdown JSONB,  -- Detailed timing breakdown: {cache: 1.2, db: 12.5, total: 15.8}
    
    -- Data sources
    sources TEXT[],  -- Array of sources: ['postgres', 'redis', 'vector_search']
    
    -- Key IDs (for filtering/replay)
    product_ids TEXT[],  -- Product IDs involved in this request
    cart_id VARCHAR(255),  -- Cart ID if applicable
    order_id VARCHAR(255),  -- Order ID if applicable
    
    -- Response summary (redacted)
    response_summary JSONB,  -- Redacted summary of response (no sensitive data)
    
    -- Metadata
    user_agent TEXT,  -- User agent string (if available)
    ip_address INET,  -- IP address (if available, for rate limiting)
    
    -- Version information
    catalog_version VARCHAR(50),
    db_version VARCHAR(50)
    -- No CHECK on tool_name so UCP and other endpoints can be logged
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_mcp_events_timestamp ON mcp_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_events_request_id ON mcp_events(request_id);
CREATE INDEX IF NOT EXISTS idx_mcp_events_tool_name ON mcp_events(tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_events_outcome_status ON mcp_events(outcome_status);
CREATE INDEX IF NOT EXISTS idx_mcp_events_product_ids ON mcp_events USING GIN(product_ids);
CREATE INDEX IF NOT EXISTS idx_mcp_events_cart_id ON mcp_events(cart_id) WHERE cart_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mcp_events_session_id ON mcp_events(session_id) WHERE session_id IS NOT NULL;

-- Composite index for common replay queries
CREATE INDEX IF NOT EXISTS idx_mcp_events_tool_timestamp ON mcp_events(tool_name, timestamp DESC);

-- Comments
COMMENT ON TABLE mcp_events IS 'Append-only event log for MCP requests/responses. Used for research replay and debugging.';
COMMENT ON COLUMN mcp_events.input_hash IS 'SHA-256 hash of input for deduplication and privacy';
COMMENT ON COLUMN mcp_events.input_summary IS 'Redacted summary of input (no sensitive data like credit cards, addresses)';
COMMENT ON COLUMN mcp_events.response_summary IS 'Redacted summary of response (no sensitive data)';
COMMENT ON COLUMN mcp_events.timings_breakdown IS 'JSON object with timing breakdown: {"cache": 1.2, "db": 12.5, "vector": 5.3, "total": 15.8}';
