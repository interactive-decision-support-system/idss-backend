# Negation and Brand Exclusion Repro Guide 

Use this to reproduce the fixes and results for brand exclusion.

## 1) Prerequisites

- Python env is set up and dependencies are installed.
- `.env` exists in `idss-backend/` with a valid `OPENAI_API_KEY`.
- Backend can reach your product database.

## 2) Start API server (required by query scripts)

From `idss-backend/`:

```bash
source .venv/bin/activate
cd mcp-server
uvicorn app.main:app --reload --port 8001
```

Keep this terminal running.

## 3) Run targeted regression unit tests

Open a second terminal at `idss-backend/`:

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest \
  agent/tests/test_negation_regressions.py \
  mcp-server/tests/test_query_parser_negation.py -q
```

## 4) Reproduce Q1 scenario results (S1-S5)

From `idss-backend/`:

```bash
python generate_brand_exclusion_query.py --n 10 > brand_exclusion_query_results.json
python generate_brand_exclusion_result.py --input brand_exclusion_query_results.json > brand_exclusion_summary.txt
```

## 5) Expected summary output

`brand_exclusion_summary.txt` should show:

- `S1 no mac: 10/10 pass`
- `S2 hate ASUS: 10/10 pass`
- `S3 steer clear standalone: 10/10 pass`
- `S3 steer clear with domain: 10/10 pass`
- `S4 no 14 standalone: 10/10 pass`
- `S4 no 14 with domain: 10/10 pass`
- `S5 override: 10/10 pass`
