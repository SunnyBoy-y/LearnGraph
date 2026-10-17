param([switch]$StartOnly)
$ErrorActionPreference = 'Stop'
$projectDirectory = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source
$pythonWindowless = Join-Path (Split-Path $pythonExecutable) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'pythonw.exe is required to run the updater without a console window.' }
$stateDirectory = Join-Path $projectDirectory 'data/updater'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$tokenPath = Join-Path $stateDirectory 'token'
if (-not (Test-Path -LiteralPath $tokenPath)) {
    Push-Location -LiteralPath $projectDirectory
    try {
        & $pythonExecutable -m updater --init-only
        if ($LASTEXITCODE -ne 0) { throw 'Updater initialization failed.' }
    } finally { Pop-Location }
}
if (-not $StartOnly) {
    $startupDirectory = [Environment]::GetFolderPath('Startup')
    $hashBytes = [System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes($projectDirectory))
    $deploymentKey = [Convert]::ToHexString($hashBytes).Substring(0, 8)
    $shellObject = New-Object -ComObject WScript.Shell
    $shortcut = $shellObject.CreateShortcut((Join-Path $startupDirectory "LearnGraph-Updater-$deploymentKey.lnk"))
    $shortcut.TargetPath = $pythonWindowless
    $shortcut.Arguments = "-m updater --project-dir `"$projectDirectory`""
    $shortcut.WorkingDirectory = $projectDirectory
    $shortcut.Description = 'Independent LearnGraph Docker updater'
    $shortcut.Save()
    Write-Output 'Updater startup shortcut installed for this Windows account.'
}
$headers = @{ Authorization = 'Bearer ' + (Get-Content -LiteralPath $tokenPath -Raw).Trim() }
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:34116/v1/health' -Headers $headers -TimeoutSec 5
    Write-Output 'Updater is already running.'
    exit 0
} catch { }
$agentProcess = Start-Process -FilePath $pythonWindowless -ArgumentList @('-m', 'updater', '--project-dir', "`"$projectDirectory`"") -WorkingDirectory $projectDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDirectory 'agent.log') -RedirectStandardError (Join-Path $stateDirectory 'agent-error.log')
$agentProcess.Id | Set-Content -LiteralPath (Join-Path $stateDirectory 'agent.pid')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:34116/v1/health' -Headers $headers -TimeoutSec 5
        Write-Output "Updater is running (PID $($agentProcess.Id))."
        exit 0
    } catch { }
    if ($agentProcess.HasExited) { throw 'Updater exited; inspect data/updater/agent-error.log.' }
}
throw 'Updater did not become available; inspect data/updater/agent-error.log.'
