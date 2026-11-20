#!/usr/bin/env bash
# Mac/Linux: ./start.sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo "Created .env — add your keys, then run again"; exit 0; fi
echo "Open http://localhost:8000"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
