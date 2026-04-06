# Q1 Reproduction Guide — Intent Recognition & Orchestration

Branch: `baani/q1-intent-recognition`
Exam: IDSS Take-Home 2026, Question 1

---

## Prerequisites

- Python 3.12+ (tested on 3.12.9; the exam spec says 3.13 also works)
- A virtual environment with dependencies installed
- No database, Redis, or OpenAI key is required to run the unit tests

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

These tests cover each of the four failing phrases from the exam, the anaphora
helper, false-positive guards, session-reset safety, and brand-exclusion fixes:

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

---

## 3. Run the full agent test suite

```bash
python -m pytest agent/tests/ -q
# Expected: 89 passed
```

---

## 4. Run the full backend test suite (exam requirement)

```bash
bash run_all_tests.sh
# Expected: 89 agent + 527 mcp-server = 616 passed, 4 skipped
```

Or equivalently:

```bash
python -m pytest mcp-server/tests/ agent/tests/ -q
```

---

## 5. How the `pythonpath` fix works

The repo has two importable packages at different directory levels:

```
idss-backend/
├── agent/                  ← importable as `agent.*`
└── mcp-server/
    └── app/                ← importable as `app.*`
```

`agent/chat_endpoint.py` imports `from app.structured_logger import StructuredLogger`.
Without `mcp-server/` on `sys.path`, pytest can't resolve `app` and every agent
test fails at collection time with `ModuleNotFoundError`.

The fix is `pytest.ini` at the repo root:

```ini
[pytest]
pythonpath = . mcp-server
```

This is the idiomatic solution for pytest ≥ 7.0 — declarative, one line, no
`sys.path` mutation in test code. No changes to `conftest.py` were needed.

---

## 6. Files changed

| File | What changed |
|------|-------------|
| `pytest.ini` | Created — `pythonpath = . mcp-server` |
| `agent/chat_endpoint.py` | `_FAST_COMPARE_KWS` + purchase idiom constants + anaphora veto + observability log |
| `agent/comparison_agent.py` | `import re` + word-boundary anaphora check in exception path |
| `agent/universal_agent.py` | Extended `_excl_kw_pat` regex |
| `agent/tests/test_chat_endpoint.py` | 9 new tests + import of `_message_references_shown_recommendation_set` |
| `agent/tests/test_universal_agent.py` | 4 new tests + module-level imports |

---

## 7. Manual verification on the live site

Go to https://idss-web.vercel.app/, ask for laptop recommendations, then try:

| Phrase | What should happen |
|--------|--------------------|
| `Lay these out side by side` | Side-by-side comparison table |
| `I'll take the second one` | Second product added to cart |
| `I've had bad experiences with Dell` | Dell excluded from next search |
| `Which of these has the best battery?` | Agent answers about shown products; session is not reset |
