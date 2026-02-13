# Complex-query routing (half-hybrid, week6tips)

**Goal:** For complex queries use `universal_agent.py`; for simple use current MCP filter path (no LLMs, faster). This helps Thomas integrate the Universal Agent for complex user messages.

## Detection

Use the helper in `app/complex_query.py`:

```python
from app.complex_query import is_complex_query

# In chat or search entry point:
if is_complex_query(message, filters=existing_filters):
    # Route to UniversalAgent (or LLM→filters)
    ...
else:
    # Use simple MCP filter path
    ...
```

**Heuristics for "complex":**

- Multiple sentences or long single sentence (> 15 words)
- Phrases like "good for", "need to run", "for machine learning", "battery life", "recommend", "which laptop"
- Many filters already present (≥ 4) from prior turns

## Wiring UniversalAgent (Thomas)

1. **When `is_complex_query(message)` is True:**
   - Call `UniversalAgent(session_id, history).process_message(message)`.
   - Response has `response_type` (`question` | `recommendations_ready`) and `filters`.
   - If `response_type == "recommendations_ready"`, run MCP search with `agent.filters` (map slot names to your filter keys), then return recommendations in the same format as current chat (e.g. `response_type="recommendations"`, `recommendations=...`).
   - If `response_type == "question"`, return the question and quick_replies to the user.

2. **When False:** Keep current flow (query_specificity, follow-up questions, or direct MCP search with parsed filters).

3. **Session:** Reuse the same `UniversalAgent` instance per session (or rehydrate from session state) so history and filters accumulate.

## Richer KG (prerequisite for complex queries)

Complex queries need **richer product features** in the KG/DB (e.g. `good_for_ml`, `battery_life`). Otherwise the UniversalAgent can extract filters, but MCP search has nothing to match. See **WEEK6_ACTION_PLAN.md** §7 (Richer knowledge graph).

## Frontend: is UniversalAgent used or overridden?

**Frontend** calls **POST /chat** on the **MCP server** (e.g. `http://localhost:8001/chat`). That is the only chat entry point; nothing overrides it.

**Same `/chat` handler** (`app/chat_endpoint.process_chat`) does two things:

| User message type | Branch | Uses UniversalAgent? |
|------------------|--------|----------------------|
| **Complex** (long, “good for ML”, “battery life”, multi-sentence, etc.) | `is_complex_query(message)` → True | **Yes** – `UniversalAgent.process_message(message)` runs; its `filters` are mapped and passed to MCP search. |
| **Simple** (e.g. “laptops”, “Gaming”, “Dell”, short answers) | `is_complex_query(message)` → False | **No** – Uses the existing MCP interview path: `detect_domain` → `is_specific_query` / `should_ask_followup` / `generate_followup_question` (no UniversalAgent). |
| **Vehicles** (after domain is vehicles) | Routed to IDSS backend (port 8000) | **No** – IDSS has its own controller; MCP just proxies the request. |

So **UniversalAgent is used for frontend queries when the message is complex**. It is not overridden; the simple path is intentional (fast filter path without LLM). To force more traffic through UniversalAgent you could relax or extend `is_complex_query` in `app/complex_query.py`.

## Complex Query Examples (from Reddit-style use cases)

These multi-constraint queries come from week6instructions.txt (Reddit-style laptop research):

| Example | Why it's complex |
|---------|------------------|
| "I will use the laptop for Webflow, Figma, Xano, Make, Python, PyCharm, and PyTorch (machine and deep learning). I expect it to handle 50 open browser tabs without issues, have a 16″ or 15.6″ screen, at least 512 GB of storage, at least 16 GB of RAM, and cost no more than $2,000." | Multiple use cases (web dev, ML), numeric constraints (storage, RAM, screen, price), multi-sentence |
| "I need a laptop for productive work—web development, QGIS, and possibly Godot or Unity—that runs Linux well, has an excellent keyboard, provides at least 8 hours of battery life, includes 32 GB of RAM, and supports a 5K ultrawide external monitor." | Use-case language, software stack (Linux, QGIS, Godot), qualitative ("excellent keyboard"), numeric (8hr battery, 32GB RAM), external display support |

## How Complex Queries Are Generated/Detected

1. **Heuristics** (`app/complex_query.py`):
   - `text.count(".") + text.count("?") >= 2` → multiple sentences → complex
   - `len(text.split()) > 15` → long single sentence → complex
   - Regex patterns in `COMPLEX_PHRASE_PATTERNS` (e.g. `\bgood for\b`, `\bneed to (run|handle|support)\b`, `\bfor (web development|machine learning|...)\b`, `\bbattery (life|lasting)\b`, etc.)
   - `len(filters) >= 4` → many filters already present → complex

2. **Routing** (`app/chat_endpoint.py`): When `is_complex_query(message)` is True, the request is routed to `UniversalAgent.process_message()`, which uses LLM to extract structured filters (e.g. `good_for_ml`, `battery_life_min_hours`, `ram_min_gb`) and passes them to MCP search.

3. **Generation (for testing)**: To create test queries, use or extend examples from `week6instructions.txt` and Reddit laptop-buying threads. The instructor suggested mimicking Reddit-style research queries where users describe their use case, constraints, and preferences in natural language.

## References

- week6tips.txt tips 15, 68, 70, 71
- `app/complex_query.py` – `is_complex_query()`, `COMPLEX_PHRASE_PATTERNS`
- `app/universal_agent.py` – UniversalAgent API
- `app/domain_registry.py` – domain schemas (laptops, books, vehicles)
