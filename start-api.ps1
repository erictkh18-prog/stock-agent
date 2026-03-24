param(
    [switch]$NoReload
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    Write-Host "Virtual environment Python not found. Falling back to system python." -ForegroundColor Yellow
    $python = "python"
}

$reloadArg = if ($NoReload) { "" } else { "--reload" }

if ([string]::IsNullOrWhiteSpace($reloadArg)) {
    & $python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
} else {
    & $python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 $reloadArg
}
