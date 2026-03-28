# IDSS Latency Benchmark - Test Instructions

## Overview

This document describes how to run the latency benchmarks for the IDSS system to measure performance across different layers of the architecture.

## Running the Benchmarks

### 1. MCP Latency Benchmark

Measures the MCP (Merchant Control Protocol) layer performance, including database queries and caching:

```bash
python scripts/run_mcp_latency_benchmark.py
```

#### Options:
- `-n N`: Number of runs per operation (default: 10)
- `--agent`: Also benchmark the agent LLM layer
- `--base-url BASE_URL`: Agent/MCP server base URL

### 2. Agent Latency Benchmark

Measures the full agent pipeline including LLM calls, session management, and search:

```bash
python scripts/run_agent_latency_benchmark.py
```

#### Options:
- `-n N`: Repetitions per phase (default: 5)
- `--no-llm`: Skip all LLM API calls (faster, DB-only)
- `--save`: Save results to agent_latency_results.json
