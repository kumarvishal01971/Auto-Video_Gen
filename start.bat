@echo off
REM Windows: double-click to start. First run installs packages.
cd /d %~dp0
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist .env (copy .env.example .env && echo Created .env - add your keys, then run again && notepad .env && exit /b)
echo Open http://localhost:8000 in your browser
python -m uvicorn app:app --host 0.0.0.0 --port 8000
