param(
  [string]$Tag = "learngraph-sandbox:local",
  [string]$ArtifactDir = ""
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$sandboxRoot = Join-Path $backendRoot "sandbox"
if (-not $ArtifactDir) {
  $ArtifactDir = Join-Path $backendRoot "data\supply-chain"
}
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null

$dockerfile = Join-Path $sandboxRoot "Dockerfile"
$npmLock = Join-Path $sandboxRoot "toolchain\package-lock.json"
if (-not (Test-Path $dockerfile) -or -not (Test-Path $npmLock)) {
  throw "Sandbox Dockerfile and toolchain package-lock.json are required."
}

docker build --tag $Tag $sandboxRoot
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

# Gate: sandboxd requires com.learngraph.runner-abi on the runner image
# (sandboxd/sandboxd/config.py RUNNER_ABI_MIN/MAX). Fail the build early if
# the label is missing or out of range so a bad image never reaches a release.
# Use JSON parsing instead of a Go-template format string: PowerShell 5.1
# mangles embedded double quotes when invoking native commands, which broke
# '{{index .Config.Labels "com.learngraph.runner-abi"}}' with
# `function "com" not defined`.
$imageInfo = docker image inspect $Tag | ConvertFrom-Json
$runnerAbi = [string]$imageInfo[0].Config.Labels.'com.learngraph.runner-abi'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($runnerAbi)) {
  throw "Runner image is missing the com.learngraph.runner-abi label; refusing to package it"
}
if ($runnerAbi -ne "1") {
  throw "Runner ABI $runnerAbi is outside the sandboxd-supported range [1, 1]"
}

$imageId = (docker image inspect --format '{{.Id}}' $Tag).Trim()
if (-not $imageId.StartsWith("sha256:")) { throw "Docker did not return immutable image ID" }

$sbom = Join-Path $ArtifactDir "sandbox.sbom.cdx.json"
$sbomAvailable = $false

# Choose an SBOM tool by probing installed Docker CLI plugins (docker info).
# docker scout ships with modern Docker Desktop and analyzes the local image
# without a registry push. docker sbom (anchore sbom-cli-plugin 0.6.0) embeds
# a Docker client speaking API 1.41 which Docker 29+ daemons (min API 1.44)
# reject, so prefer scout whenever present to avoid a guaranteed failure plus
# scary stderr on every run.
$plugins = @(docker info --format '{{json .ClientInfo.Plugins}}' | ConvertFrom-Json)
$hasScout = @($plugins | Where-Object { $_.Name -eq "scout" }).Count -gt 0
$hasSbom  = @($plugins | Where-Object { $_.Name -eq "sbom" }).Count -gt 0

if ($hasScout) {
  docker scout sbom --format cyclonedx --output $sbom $Tag
} elseif ($hasSbom) {
  docker sbom --format cyclonedx-json $Tag --output $sbom
}
# scout may exit non-zero even when the SBOM file was written (e.g. a failed
# cleanup of its temp archive on Windows), so success is judged by the output
# file, not the exit code.
if ((Test-Path $sbom) -and (Get-Item $sbom).Length -gt 1KB) { $sbomAvailable = $true }

if (-not $sbomAvailable) {
  # stderr from the tool above stays visible; this warning makes the degraded
  # manifest explicit instead of silently missing the SBOM.
  Write-Warning "SBOM generation unavailable (no usable docker scout/sbom plugin); emitting source-input manifest without full image SBOM."
}

$inputs = @{
  dockerfile_sha256 = (Get-FileHash $dockerfile -Algorithm SHA256).Hash.ToLower()
  npm_lock_sha256 = (Get-FileHash $npmLock -Algorithm SHA256).Hash.ToLower()
}
$manifest = @{
  schema = "learngraph-sandbox-supply-chain-v1"
  image_id = $imageId
  tag = $Tag
  built_at = [DateTime]::UtcNow.ToString("o")
  inputs = $inputs
  sbom_file = if ($sbomAvailable) { [IO.Path]::GetFileName($sbom) } else { $null }
  sbom_sha256 = if ($sbomAvailable) { (Get-FileHash $sbom -Algorithm SHA256).Hash.ToLower() } else { $null }
  signing = @{
    status = "unsigned-local"
    note = "Use cosign with a trusted registry manifest digest and externally managed key/OIDC identity."
  }
}
$manifestPath = Join-Path $ArtifactDir "sandbox.supply-chain.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding utf8
Write-Host "Image: $imageId"
Write-Host "Manifest: $manifestPath"
if ($sbomAvailable) { Write-Host "SBOM: $sbom" }
