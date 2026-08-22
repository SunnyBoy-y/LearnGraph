param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')]
    [string]$Version,

    [string]$Repository = "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph",

    [string]$SourceImage = "learngraph-sandbox:local",

    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$sandboxRoot = Join-Path $backendRoot "sandbox"
$target = "$($Repository.ToLower()):$Version"

$sourceInfo = docker image inspect $SourceImage --format '{{.Id}}|{{.Architecture}}|{{.Os}}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceInfo)) {
    throw "Source sandbox image was not found: $SourceImage"
}
$id, $architecture, $os = $sourceInfo.Trim().Split('|', 3)
if ($architecture -ne 'amd64' -or $os -ne 'linux') {
    throw "ACR Personal Edition release preparation currently supports linux/amd64 only; source is $architecture/$os"
}

# Hardened smoke via the docker CLI. The backend ships only the sandboxd control
# plane by default (no python `docker` SDK), so this script must never import
# app.services.sandbox_bootstrap. The container options mirror the production
# SandboxCreateSpec defaults (network none, read-only root, non-root 65532,
# cap-drop ALL, per-runtime seccomp profile, shm budget, tmpfs /tmp).
# NOTE: keep this file pure ASCII; Windows PowerShell 5.1 mis-parses UTF-8
# without BOM when non-ASCII text appears inside string literals.
function Invoke-RunnerCheck {
    param(
        [string]$Image,
        [string]$Seccomp,
        [string]$Shm,
        [string[]]$Cmd
    )
    $output = & docker run --rm --network none --read-only --user 65532:65532 `
        --cap-drop ALL `
        --security-opt "seccomp=$Seccomp" --security-opt no-new-privileges:true `
        --memory 2g --memory-swap 2g --pids-limit 1024 --shm-size $Shm `
        --tmpfs "/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777" `
        --env HOME=/tmp --env XDG_CONFIG_HOME=/tmp/.config --env XDG_CACHE_HOME=/tmp/.cache `
        $Image @Cmd 2>&1
    return @{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
}

if (-not $SkipSmoke) {
    $codeProfile = Join-Path $sandboxRoot "seccomp_profile_code.json"
    $browserProfile = Join-Path $sandboxRoot "seccomp_profile.json"
    $nodeToolchain = "Promise.all(['vite','vue','react','react-dom'," +
        "'@vitejs/plugin-vue','@vitejs/plugin-react','vite-plugin-singlefile']" +
        ".map((m) => import(m))).then(() => process.exit(0))" +
        ".catch((e) => { console.error(e); process.exit(1); })"
    $pythonImport = "import av, bs4, docx, fitz, mammoth, markdown_it, numpy, odf, " +
        "openpyxl, pandas, pdfplumber, PIL, pydub, pypdf, pptx, pyxlsb, trafilatura, " +
        "xlsxwriter, learngraph_tasks"

    $checks = @(
        @{ Name = "code:python";     Seccomp = $codeProfile;    Shm = "64m"; Cmd = @("python", "--version") },
        @{ Name = "code:node";       Seccomp = $codeProfile;    Shm = "64m"; Cmd = @("node", "--version") },
        @{ Name = "code:ffmpeg";     Seccomp = $codeProfile;    Shm = "64m"; Cmd = @("ffmpeg", "-version") },
        @{ Name = "code:pyimports";  Seccomp = $codeProfile;    Shm = "64m"; Cmd = @("python", "-c", $pythonImport) },
        @{ Name = "code:nodekit";    Seccomp = $codeProfile;    Shm = "64m"; Cmd = @("node", "-e", $nodeToolchain) },
        @{ Name = "browser:python";  Seccomp = $browserProfile; Shm = "1g";  Cmd = @("python", "--version") },
        @{ Name = "browser:nodekit"; Seccomp = $browserProfile; Shm = "1g";  Cmd = @("node", "-e", $nodeToolchain) },
        @{ Name = "browser:chromium"; Seccomp = $browserProfile; Shm = "1g"; Cmd = @("node", "/opt/learngraph/browser-smoke.js") }
    )

    Write-Output "Running hardened sandbox smoke via docker CLI (no Docker SDK dependency)..."
    foreach ($check in $checks) {
        $result = Invoke-RunnerCheck -Image $id -Seccomp $check.Seccomp -Shm $check.Shm -Cmd $check.Cmd
        if ($result.ExitCode -ne 0) {
            $detail = if ([string]::IsNullOrWhiteSpace($result.Output)) { "non-zero exit" } else { $result.Output }
            throw "Refusing to tag an image whose hardened sandbox smoke failed: $($check.Name)`n$detail"
        }
        Write-Output "  smoke [$($check.Name)] passed"
    }
    Write-Output "Hardened sandbox smoke passed."
}

# This tool intentionally only creates a local tag. It never runs docker login,
# docker push, or any registry write operation.
& docker tag $SourceImage $target
if ($LASTEXITCODE -ne 0) {
    throw "Could not create local ACR release tag: $target"
}

Write-Output "Prepared local ACR release tag: $target"
Write-Output "Architecture: linux/amd64"
Write-Output "Source image ID: $id"
Write-Output ""
Write-Output "No image was pushed and no ACR login was attempted."
Write-Output "When you are ready, run these commands yourself:"
Write-Output "  docker login --username=<YOUR_ALIYUN_ACCOUNT> $($target.Split('/')[0])"
Write-Output "  docker push $target"
Write-Output ""
Write-Output "After the push finishes, obtain the immutable RepoDigest locally:"
Write-Output "  docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' $target"
Write-Output "Or copy the digest shown on the ACR console (image version -> digest)."
Write-Output ""
Write-Output "Consumers need NO configuration: the release default lives in code"
Write-Output "  (backend/app/core/config.py -> DEFAULT_SANDBOX_PREBUILT_IMAGE), so upgrading"
Write-Output "  the code picks up this runner version automatically. A version tag works"
Write-Output "  for first-time bootstrap: the application pulls it, resolves the immutable"
Write-Output "  RepoDigest, runs hardened smoke, and persists only that digest."
Write-Output ""
Write-Output "Only pin a specific deployment explicitly (admin lock) via env:"
Write-Output "  LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=$target"
$repoOnly = $target.Split(':')[0]
Write-Output "  LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=${repoOnly}@sha256:<repo-digest>"
