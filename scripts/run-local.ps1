param([string]$PythonExecutable = '', [switch]$WaitFor1430)
$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $projectPath '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $PythonExecutable)) { throw 'Run scripts/setup.ps1 first or pass -PythonExecutable.' }
Push-Location -LiteralPath $projectPath
try {
    $arguments = @('-m','etf_assistant.cli','collect','--retry-window','--evidence','reports/local-raw.json')
    if ($WaitFor1430) { $arguments += '--wait' }
    & $PythonExecutable @arguments
    $resultCode = $LASTEXITCODE
    if ($resultCode -ne 0) { Write-Output 'Collection unavailable. Check public/latest.json for validation reasons.' }
    exit $resultCode
} finally { Pop-Location }
