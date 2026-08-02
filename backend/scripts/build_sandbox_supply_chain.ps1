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

$imageId = (docker image inspect --format '{{.Id}}' $Tag).Trim()
if (-not $imageId.StartsWith("sha256:")) { throw "Docker did not return immutable image ID" }

$sbom = Join-Path $ArtifactDir "sandbox.sbom.cdx.json"
$sbomAvailable = $false
try {
  docker sbom --format cyclonedx-json $Tag --output $sbom
  if ($LASTEXITCODE -eq 0 -and (Test-Path $sbom)) { $sbomAvailable = $true }
} catch {
  Write-Warning "docker sbom unavailable; emitting source-input manifest without full image SBOM."
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
