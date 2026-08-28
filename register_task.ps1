# register_task.ps1 — register (or remove) the daily "AI Daily News" scheduled task.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File register_task.ps1               # register at 08:00
#   powershell -ExecutionPolicy Bypass -File register_task.ps1 -Time "09:30" # custom time
#   powershell -ExecutionPolicy Bypass -File register_task.ps1 -Unregister  # remove the task
#
# After registering, you can test it immediately with:
#   Start-ScheduledTask -TaskName "AI Daily News"
#   Get-ScheduledTaskInfo -TaskName "AI Daily News"

param(
    [string]$TaskName = "AI Daily News",
    [string]$Time = "08:00",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $here "main.py"

if (-not (Test-Path $scriptPath)) {
    Write-Error "main.py not found next to this script: $scriptPath"
    exit 1
}

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Task '$TaskName' removed (or was not present)." -ForegroundColor Green
    exit 0
}

# Locate the Python interpreter.
# 1) Prefer the project virtualenv (if dependencies were installed there).
# 2) Otherwise use the global python. Prefer pythonw.exe so no console window pops.
$venvPythonw = Join-Path $here ".venv\Scripts\pythonw.exe"
$venvPython = Join-Path $here ".venv\Scripts\python.exe"
if (Test-Path $venvPythonw) {
    $exe = $venvPythonw
} elseif (Test-Path $venvPython) {
    $exe = $venvPython
} else {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) {
        Write-Error "python not found on PATH and no .venv found. Install Python 3.10+ from https://www.python.org, then re-run."
        exit 1
    }
    $pythonDir = Split-Path -Parent $python
    $pythonw = Join-Path $pythonDir "pythonw.exe"
    $exe = if (Test-Path $pythonw) { $pythonw } else { $python }
}

# Validate time format HH:MM
if ($Time -notmatch '^\d{1,2}:\d{2}$') {
    Write-Error "Time must be in HH:MM 24h format, e.g. 08:00. Got: $Time"
    exit 1
}

$action = New-ScheduledTaskAction -Execute $exe -Argument "`"$scriptPath`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Collect AI news worldwide daily and generate a bilingual (中文/EN) Markdown report." `
    -Force | Out-Null

Write-Host ""
Write-Host "[OK] Daily task registered:" -ForegroundColor Green
Write-Host "   Task name : $TaskName"
Write-Host "   Run time  : every day at $Time"
Write-Host "   Command   : $exe $scriptPath"
Write-Host "   Working dir: $here"
Write-Host ""
Write-Host "Important: make sure your LLM API key is set as a USER environment variable"
Write-Host "so the scheduled task can read it. Example:"
Write-Host "   setx DEEPSEEK_API_KEY ""sk-xxxx""   (then log off/on or restart shell)"
Write-Host ""
Write-Host "Test now with:  Start-ScheduledTask -TaskName ""$TaskName"""
Write-Host "Check status:   Get-ScheduledTaskInfo -TaskName ""$TaskName"""
