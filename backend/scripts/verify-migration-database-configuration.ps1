param(
    [int]$Port = 8017
)

$ErrorActionPreference = "Stop"
$runId = [guid]::NewGuid().ToString("N")
$dataRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\data")).Path
$testRoot = Join-Path $dataRoot ("integration-db-config-" + $runId)
New-Item -ItemType Directory -Path $testRoot | Out-Null
$dbPath = Join-Path $testRoot "test.db"
$stdoutPath = Join-Path $testRoot "server.out.log"
$stderrPath = Join-Path $testRoot "server.err.log"
$backendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $backendRoot ".venv\Scripts\python.exe"
$plainPassword = "integration-$runId"

$env:LEARNGRAPH_DATABASE_URL = "sqlite:///" + ($dbPath -replace "\\", "/")
$env:LEARNGRAPH_STORAGE_ROOT = Join-Path $testRoot "storage"
$env:LEARNGRAPH_MEMORY_ROOT = Join-Path $testRoot "memory"
$env:LEARNGRAPH_SECRET_PROVIDER = "environment"
$env:LEARNGRAPH_MASTER_KEY = "integration-only-master-key-32-bytes-minimum"
$env:LEARNGRAPH_MASTERY_EMBEDDED_SCHEDULER_ENABLED = "false"
$env:LEARNGRAPH_MEMORY_RETENTION_SCHEDULER_ENABLED = "false"
$env:LEARNGRAPH_SANDBOX_CLEANUP_SCHEDULER_ENABLED = "false"

$server = Start-Process `
    -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port" `
    -WorkingDirectory $backendRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$Port/api/v1/health" `
                -TimeoutSec 1
            if ($health.status -eq "ok") {
                $ready = $true
                break
            }
        } catch {
            # The real server is still starting.
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) {
        throw "Isolated backend did not become healthy. See $stderrPath"
    }

    $registerBody = @{
        username = "cfg_" + $runId.Substring(0, 10)
        display_name = "DB Config Integration"
        password = "Integration-Password-$runId"
    } | ConvertTo-Json
    $auth = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:$Port/api/v1/auth/register" `
        -ContentType "application/json" `
        -Body $registerBody
    $headers = @{
        Authorization = "Bearer " + $auth.access_token
        "X-Workspace-ID" = $auth.default_workspace_id
    }

    $initial = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:$Port/api/v1/migrations/database-configurations" `
        -Headers $headers
    if (@($initial).Count -ne 0) {
        throw "Expected a new workspace to have no database configurations"
    }

    $payload = @{
        host = "127.0.0.1"
        port = 1
        database_name = "learngraph"
        username = "migration_user"
        password = $plainPassword
        ssl_mode = "require"
    } | ConvertTo-Json
    $saved = Invoke-RestMethod `
        -Method Put `
        -Uri "http://127.0.0.1:$Port/api/v1/migrations/database-configurations/postgresql" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $payload
    if ($saved.connection_verified -ne $false -or $saved.password_configured -ne $true) {
        throw "The unreachable real target was not reported truthfully"
    }
    $serialized = $saved | ConvertTo-Json -Depth 8
    if ($serialized.Contains($plainPassword) -or $serialized -match "ciphertext|password_ciphertext") {
        throw "The save response leaked secret material"
    }

    $listed = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:$Port/api/v1/migrations/database-configurations" `
        -Headers $headers
    if (@($listed).Count -ne 1) {
        throw "The saved database configuration was not persisted"
    }
    $otherRegisterBody = @{
        username = "other_" + $runId.Substring(0, 10)
        display_name = "Other Workspace"
        password = "Other-Integration-Password-$runId"
    } | ConvertTo-Json
    $otherAuth = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:$Port/api/v1/auth/register" `
        -ContentType "application/json" `
        -Body $otherRegisterBody
    $crossWorkspaceHeaders = @{
        Authorization = "Bearer " + $otherAuth.access_token
        "X-Workspace-ID" = $auth.default_workspace_id
    }
    $crossWorkspaceStatus = 0
    try {
        Invoke-RestMethod `
            -Method Get `
            -Uri "http://127.0.0.1:$Port/api/v1/migrations/database-configurations" `
            -Headers $crossWorkspaceHeaders | Out-Null
    } catch {
        $crossWorkspaceStatus = [int]$_.Exception.Response.StatusCode
    }
    if ($crossWorkspaceStatus -ne 403) {
        throw "Cross-workspace database configuration access was not rejected"
    }
    $adapters = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:$Port/api/v1/migrations/adapters" `
        -Headers $headers
    $postgres = $adapters | Where-Object {
        $_.provider_kind -eq "postgresql" -and $_.capability -eq "database"
    }
    if (-not $postgres.configured -or $postgres.connection_verified) {
        throw "Adapter inventory did not consume the workspace configuration"
    }

    $preflightBody = @{
        source_kind = "sqlite"
        target_kind = "postgresql"
        resource_kind = "database"
    } | ConvertTo-Json
    $job = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:$Port/api/v1/migrations/preflight" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $preflightBody
    if ($job.status -ne "preflight_blocked" -or $job.report.ready -ne $false) {
        throw "Preflight did not block an unreachable configured target"
    }

    $databaseCheck = @'
import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
s=c.execute('select password_ciphertext from infrastructure_database_configurations').fetchone()
a=chr(32).join(str(r) for r in c.execute('select details from audit_events'))
assert s and s[0] and sys.argv[2] not in s[0]
assert sys.argv[2] not in a
'@
    & $python -c $databaseCheck $dbPath $plainPassword
    if ($LASTEXITCODE -ne 0) {
        throw "Encrypted secret or audit redaction verification failed"
    }

    Write-Output "workspace_scope_and_cross_workspace_rejection=passed"
    Write-Output "encrypted_secret_and_audit_redaction=passed"
    Write-Output "failed_connection_status=passed"
    Write-Output "adapter_workspace_configuration=passed"
    Write-Output "preflight_blocked_boundary=passed"
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
        $server.WaitForExit()
    }
    $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
    $resolvedDataRoot = [System.IO.Path]::GetFullPath($dataRoot)
    if (-not $resolvedTestRoot.StartsWith(
        $resolvedDataRoot + [System.IO.Path]::DirectorySeparatorChar
    )) {
        throw "Refusing cleanup outside the backend data root"
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}
