backend := "apps/backend"

dev:
    cd {{backend}} && python -m uvicorn merchant_agent.main:app --reload

test:
    cd {{backend}} && python -m pytest

compile:
    python -m compileall {{backend}}

enrich *args:
    cd {{backend}} && python scripts/run_enrichment.py {{args}}
