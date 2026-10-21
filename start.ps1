# Tablero DAB-DPO — Arranque completo
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Iniciando backend (FastAPI)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\backend'; python -m uvicorn app.main:app --reload --port 8000" -WindowStyle Normal

Start-Sleep 2

Write-Host "Iniciando frontend (React)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev" -WindowStyle Normal

Start-Sleep 3
Write-Host ""
Write-Host "Tablero disponible en: http://localhost:5173" -ForegroundColor Green
Start-Process "http://localhost:5173"
