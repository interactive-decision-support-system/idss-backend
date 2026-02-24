#!/bin/bash

# Activate virtual environment if exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run all pytest tests in backend and agent
# Exclude test_vector_search.py: it can segfault (native deps: sentence-transformers/torch) in some envs
pytest mcp-server/tests agent/tests --ignore=mcp-server/tests/test_vector_search.py "$@"

# Print summary
if [ $? -eq 0 ]; then
    echo "All tests passed!"
else
    echo "Some tests failed. See above for details."
fi
