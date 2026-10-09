param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $PythonExecutable) {
    $bundledPath = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPath) {
        $PythonExecutable = $bundledPath
    } else {
        $PythonExecutable = 'python'
    }
}
Push-Location -LiteralPath $projectPath
try {
    & $PythonExecutable -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is unavailable. Pass -PythonExecutable with its full path.' }
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & $PythonExecutable -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    & './.venv/Scripts/python.exe' -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & './.venv/Scripts/python.exe' -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
    & './.venv/Scripts/python.exe' -m pytest -q --junitxml=reports/offline-tests.xml
    if ($LASTEXITCODE -ne 0) { throw 'Offline validation failed.' }
    Write-Output 'Setup complete. Scheduler and notifications are not activated.'
} finally { Pop-Location }
