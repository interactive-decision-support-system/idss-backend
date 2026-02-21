#!/bin/bash

# Activate virtual environment if exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run all pytest tests in backend and agent
pytest mcp-server/tests agent/tests "$@"

# Print summary
if [ $? -eq 0 ]; then
    echo "All tests passed!"
else
    echo "Some tests failed. See above for details."
fi
