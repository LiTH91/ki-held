#!/bin/bash

# Check if uv is installed, if not, install it
if ! command -v uv &> /dev/null
then
    echo "uv could not be found, installing..."
    pip install uv
fi

# Install dependencies
uv pip install -r requirements.txt

# Print next steps
cat << EOF

Setup complete. To run the FastAPI server, execute:

cd backend
uv run uvicorn main:app --reload --port 8000
EOF

