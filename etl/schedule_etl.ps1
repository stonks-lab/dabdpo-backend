# Registra las tareas programadas del ETL (ejecutar como Administrador)
#
# Tarea 1 (06:30): ETL Power BI → d2.parquet (cuando esté configurado)
# Tarea 2 (07:00): Auto-recarga d2.parquet → dbo.proyecciones si el parquet es más nuevo

$EtlDir     = "C:\Dev\Tablero-integrado\tablero-web\etl"
$Python     = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source ?? "python"
$LogFile    = "$EtlDir\etl.log"

# ── Tarea 1: ETL Power BI ────────────────────────────────────────────────────
$Task1Name   = "Tablero DAB-DPO — ETL Proyecciones PBI"
$EtlScript   = "$EtlDir\etl_proyecciones.py"

$Action1 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$EtlScript`" >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $EtlDir

$Trigger1 = New-ScheduledTaskTrigger -Daily -At "06:30"

$Settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $Task1Name `
    -Action   $Action1 `
    -Trigger  $Trigger1 `
    -Settings $Settings1 `
    -RunLevel Highest `
    -Force

Write-Host "Tarea '$Task1Name' registrada — corre diariamente a las 06:30" -ForegroundColor Green

# ── Tarea 2: Auto-recarga parquet → SQL ─────────────────────────────────────
$Task2Name    = "Tablero DAB-DPO — Auto-recarga Proyecciones SQL"
$ReloadScript = "$EtlDir\auto_reload_proyecciones.py"

$Action2 = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$ReloadScript`" >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $EtlDir

# Corre 30 min después del ETL, para que el parquet ya esté actualizado
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "07:00"

$Settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $Task2Name `
    -Action   $Action2 `
    -Trigger  $Trigger2 `
    -Settings $Settings2 `
    -RunLevel Highest `
    -Force

Write-Host "Tarea '$Task2Name' registrada — corre diariamente a las 07:00" -ForegroundColor Green
Write-Host "Log compartido: $LogFile" -ForegroundColor Cyan
