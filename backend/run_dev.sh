#!/usr/bin/env bash
# Local development server with auto-reload.
cd "$(dirname "$0")"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/uvicorn geoagent.main:app --reload --host 127.0.0.1 --port 8000
