# setup_servicio.ps1
# Registra el Tablero DAB-DPO como tarea en el Programador de Tareas de Windows.
# Ejecutar UNA sola vez como Administrador:
#   powershell -ExecutionPolicy Bypass -File setup_servicio.ps1

$TaskName   = "TableroDabDpo"
$BatFile    = "C:\Dev\Tablero-integrado\tablero-web\run_servicio.bat"
$PythonExe  = (Get-Command python).Source

# Eliminar tarea anterior si existe
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea anterior eliminada."
}

$Action  = New-ScheduledTaskAction -Execute $BatFile
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action   $Action `
    -Trigger  $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Tablero Integrado DAB-DPO — FastAPI en puerto 8000"

Write-Host ""
Write-Host "Tarea '$TaskName' registrada." -ForegroundColor Green
Write-Host "La plataforma arrancara automaticamente con Windows en http://localhost:8000"
Write-Host ""
Write-Host "Comandos utiles:"
Write-Host "  Iniciar ahora  : Start-ScheduledTask  -TaskName $TaskName"
Write-Host "  Detener        : Stop-ScheduledTask   -TaskName $TaskName"
Write-Host "  Ver estado     : Get-ScheduledTask    -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  Desregistrar   : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
