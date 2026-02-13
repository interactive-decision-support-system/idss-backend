# Knowledge Graph: kg.txt Compliance & Source Research

## kg.txt Requirements

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| **Knowledge graphs for new products** | ✅ Done | `build_knowledge_graph_all.py` – laptops, books, jewelry, accessories, electronics, Beauty, Clothing, Art, Food |
| **Session intent** (big goal) | ✅ Done | `Explore` \| `Decide today` \| `Execute purchase` – in `InterviewSessionState` + Neo4j `SessionIntent` nodes |
| **Step intent** (next action) | ✅ Done | `Research` \| `Compare` \| `Negotiate` \| `Schedule` \| `Return/post-purchase` – in `InterviewSessionState` + Neo4j `StepIntent` nodes |
| **Track important info across session** | ✅ Done | `get_important_info_for_next_meeting()` + `create_session_memory()` in Neo4j |
| **Sources researched** | ✅ Done | MemOS, Zep, OpenClaw – see below |

---

## Source Research Summary

### 1. MemOS (MemTensor/MemOS)

- **What it is:** Memory OS for LLMs/agents – unified add/retrieve/edit/delete memory, structured as a graph.
- **Concepts used:**
  - Graph-structured memory (not black-box embeddings)
  - `user_id` + `mem_cube_id` for isolation
  - Memory feedback & correction
  - Multi-cube knowledge base
  - Uses Neo4j + Qdrant
- **Applied:** `UserSession` nodes with `user_id`, `session_id`, `important_info` JSON; `SessionIntent` and `StepIntent` as graph nodes.

### 2. Zep (getzep/zep)

- **What it is:** Context engineering platform – Graph RAG, temporal knowledge graph, relationship-aware retrieval.
- **Concepts used:**
  - Temporal facts: `valid_at` / `invalid_at` (Graphiti)
  - Relationship-aware context
  - Sub-200ms latency target
- **Applied:** `UserSession.updated_at` for recency; design allows future `valid_at`/`invalid_at` for temporal facts.

### 3. OpenClaw (openclaw/openclaw)

- **What it is:** Personal AI assistant – multi-channel (WhatsApp, Telegram, Slack, etc.), session model, Gateway.
- **Concepts used:**
  - Session model with `main` / group isolation
  - MemOS Cloud OpenClaw Plugin: recall before agent, save after
  - Cross-channel (WhatsApp, etc.)
- **Applied:** Session recall before agent (`get_session_memory()`), save after (`create_session_memory()`); session model in `InterviewSessionManager`.

---

## Neo4j Schema (Session/Memory)

```
(User)-[:HAS_SESSION]->(UserSession)
(UserSession)-[:HAS_SESSION_INTENT]->(SessionIntent)
(UserSession)-[:HAS_STEP_INTENT]->(StepIntent)

UserSession: session_id, session_intent, step_intent, important_info (JSON), updated_at
SessionIntent: name (Explore | Decide today | Execute purchase)
StepIntent: name (Research | Compare | Negotiate | Schedule | Return/post-purchase)
```

---

## Usage

```python
# Persist session memory (e.g. at end of turn or session end)
from app.knowledge_graph import KnowledgeGraphBuilder
from app.neo4j_config import Neo4jConnection
from app.interview.session_manager import get_session_manager

builder = KnowledgeGraphBuilder(Neo4jConnection())
sm = get_session_manager()
important = sm.get_important_info_for_next_meeting(session_id)
builder.create_session_memory(
    session_id=session_id,
    user_id=user_id or "anonymous",
    session_intent=state.session_intent or "Explore",
    step_intent=state.step_intent or "Research",
    important_info=important,
)

# Recall before agent (MemOS-style)
memory = builder.get_session_memory(session_id)
if memory:
    # Use memory["session_intent"], memory["step_intent"], memory["important_info"]
    pass
```

---

## Optional Future Enhancements

- **Temporal facts (Zep):** Add `valid_at` / `invalid_at` to facts for time-aware reasoning.
- **Memory feedback (MemOS):** Natural-language correction/refinement of stored memories.
- **Cross-channel (OpenClaw):** Support WhatsApp/Telegram/etc. channels with shared session memory.
