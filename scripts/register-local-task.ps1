# Optional installer. It has NOT been run during project delivery.
param([string]$TaskName = 'A-share ETF 1430 collector')
$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe'
$runnerPath = Join-Path $projectPath 'scripts/run-local.ps1'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'This task requires Windows timezone China Standard Time (UTC+08:00).' }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Task already exists; choose another TaskName or update it manually.' }
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $runnerPath + '" -WaitFor1430'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '14:28'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -MultipleInstances IgnoreNew
# Interactive-token task: no stored password and no credentials requested.
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Wait until 14:30 Asia/Shanghai; verify real trading calendar and source timestamps; write JSON.' | Select-Object TaskName,State
Write-Output 'Local collection scheduled. This does not activate ChatGPT or phone notifications.'
