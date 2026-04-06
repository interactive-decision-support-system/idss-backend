# Q1 Reproduction Guide — Intent Recognition & Orchestration

Branch: `baani/q1-intent-recognition`

---

## Prerequisites

- Python 3.12+ with a virtual environment (`python3 -m venv .venv`)
- No database, Redis, or OpenAI key required to run the unit tests

---

## 1. Clone and set up

```bash
git clone https://github.com/BaaniLeen/idss-backend.git
cd idss-backend
git checkout baani/q1-intent-recognition

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r mcp-server/requirements.txt
pip install -r agent/requirements.txt
```

---

## 2. Run the Q1-specific tests

These tests cover the four failing phrases from the exam, the anaphora helper, false-positive guards, session-reset safety, and brand-exclusion fixes:

```bash
python -m pytest \
  agent/tests/test_chat_endpoint.py::test_post_rec_compare_lay_these_out_fast_path \
  agent/tests/test_chat_endpoint.py::test_add_to_cart_ill_take_second_no_cart_keyword \
  agent/tests/test_chat_endpoint.py::test_add_to_cart_ill_take_it_defaults_to_first \
  agent/tests/test_chat_endpoint.py::test_post_rec_anaphora_downgrades_new_search_no_session_reset \
  agent/tests/test_chat_endpoint.py::test_genuine_new_search_without_anaphora_resets_session \
  agent/tests/test_chat_endpoint.py::test_purchase_idiom_conditional_clause_not_add_to_cart \
  agent/tests/test_chat_endpoint.py::test_purchase_idiom_preference_statement_not_add_to_cart \
  agent/tests/test_chat_endpoint.py::test_message_references_shown_recommendation_set \
  agent/tests/test_universal_agent.py::test_excluded_brands_bad_experiences_with_brand_regex \
  agent/tests/test_universal_agent.py::test_excluded_brands_awful_adjective_not_captured \
  agent/tests/test_universal_agent.py::test_excluded_brands_poor_adjective_not_captured \
  agent/tests/test_universal_agent.py::test_excluded_brands_generic_sentiment_not_captured \
  -v
```

Expected: **12 passed**.

---

## 3. Run the full agent test suite

```bash
python -m pytest agent/tests/ -q
# Expected: 89 passed
```

---

## 4. Run the full backend test suite (exam requirement)

```bash
python -m pytest mcp-server/tests/ agent/tests/ -q
# Expected: 616 passed, 4 skipped
```

> **Note:** Always use `python -m pytest` (not bare `pytest`). The `pytest` binary
> shebang may point to a different Python interpreter than your active venv.

---

## 5. Files changed

| File | What changed |
|------|-------------|
| `agent/chat_endpoint.py` | `_FAST_COMPARE_KWS` + `_PURCHASE_IDIOMS_RE` + `_CASUAL_TAKE_DEFAULT_RE` + `_message_references_shown_recommendation_set()` + anaphora veto + observability log |
| `agent/comparison_agent.py` | Word-boundary anaphora check in exception path; circular import reverted; redundant `bool()` removed |
| `agent/universal_agent.py` | Extended `_EXCL_KW_PAT` with bridging-phrase suffix; removed `awful`; added `poor`; hoisted `_KNOWN_BRANDS_LIST` to module level |
| `agent/tests/test_chat_endpoint.py` | 8 new tests; fixed duplicate assertion; fixed mock return type |
| `agent/tests/test_universal_agent.py` | 4 new brand-exclusion tests |

---

## 6. Manual verification

Start the backend with `python -m uvicorn app.main:app --app-dir mcp-server --port 8001`, ask for laptop recommendations, then try:

| Phrase | Expected behaviour |
|--------|--------------------|
| `Lay these out side by side` | Side-by-side comparison table |
| `I'll take the second one` | Second product added to cart |
| `I've had bad experiences with Dell` | Dell excluded from next search |
| `Which of these has the best battery?` | Agent answers about shown products; session is **not** reset |
