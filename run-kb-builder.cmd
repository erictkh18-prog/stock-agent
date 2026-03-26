@echo off
setlocal
cd /d "%~dp0"

echo Starting Knowledge Base Builder on http://127.0.0.1:8000/knowledge-base-builder
echo Press Ctrl+C to stop.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
) else (
  python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
)

endlocal
