# Merchant Agent Flowchart

Figure asset: [merchant_agent_flowchart.svg](/Users/golrezae/Documents/workspace/projects/Archive/idss/idss-old/idss-backend/mcp-server/docs/merchant_agent_flowchart.svg)

## Suggested Figure Caption

High-level architecture of the merchant agent implemented in the MCP server. The merchant agent exposes the product catalog through MCP, UCP, and ACP interfaces, interprets user queries through normalization, filter extraction, and domain/session logic, retrieves candidates from the catalog plus auxiliary retrieval systems, ranks results, and returns structured product or commerce responses. Dashed arrows indicate optional follow-up questioning when the initial request is underspecified.

## High-Level Component Summary

1. Interface layer
   The merchant agent exposes a canonical MCP tool surface and wraps the same core operations with UCP and ACP protocol adapters. Tool schema discovery makes the same merchant functions consumable by different LLM providers.

2. Orchestration and query understanding
   A request orchestrator dispatches the request into search, product detail, or commerce execution paths. For search, the system normalizes the query, extracts structured filters and hardware specs, routes by domain, and optionally enters an interview/session loop when the query is underspecified.

3. Retrieval and ranking
   The catalog search engine combines structured catalog filtering with optional knowledge-graph candidates, vector retrieval, progressive relaxation, and IDSS-based ranking. This layer produces the final ordered set of product summaries, detail payloads, and recommendation reasons.

4. Response generation
   A response builder formats the final result into MCP, UCP, or ACP-compatible envelopes and can also return a follow-up question instead of products when the merchant agent determines that more constraints are needed.

## Short Paper-Ready Explanation

The merchant agent acts as the merchant-side decision and execution layer. Its main role is to convert agent-facing requests into structured catalog operations while preserving merchant constraints such as IDs-only execution, inventory-aware checkout, and protocol-specific response contracts. Internally, it separates protocol handling, request orchestration, query understanding, retrieval/ranking, and response generation so that the same merchant catalog can be served consistently across conversational search, product detail lookup, and commerce execution.
