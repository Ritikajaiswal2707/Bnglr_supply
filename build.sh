#!/usr/bin/env bash
# Render build step: build the React app, install Python dependencies, copy the build next to the API.
set -euo pipefail
(cd frontend && npm ci --no-audit --no-fund && npm run build)
pip install -r backend/requirements.txt
mkdir -p backend/static
rm -rf backend/static/*
cp -r frontend/dist/* backend/static/
