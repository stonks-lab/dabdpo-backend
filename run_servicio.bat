@echo off
cd /d "C:\Dev\Tablero-integrado\tablero-web\backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
