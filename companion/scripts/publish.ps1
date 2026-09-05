#!/usr/bin/env powershell
# companion/scripts/publish.ps1
# Publishes signed artifacts from the latest build to a GitHub release:
#   1. GitHub release on slayner/ziggs (artifacts and signatures)
#   2. Updates and publishes companion-release.json with the discovered artifacts
#
# Usage: cd companion ; powershell -ExecutionPolicy Bypass -File scripts/publish.ps1 [-Notes "text"]
# Prerequisite: build completed with the signing key available in the environment

param(
    [string]$Notes = ""
)

$VpsHost = "root@167.233.241.191"
$SshKey = Join-Path $HOME ".ssh/hetzner_ziggs"
$VpsManifestPath = "/home/ziggs/ziggs/backend/data/companion-release.json"

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot/..

# --- Load Companion configuration ---
$tauriConf = Get-Content "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json
$version = $tauriConf.version

Write-Host "=== Publishing Companion v$version ===" -ForegroundColor Cyan

# --- Find signed artifacts from the current build ---
$artifactSpecs = @(
    @{ Platform = "windows-x86_64"; Directory = "src-tauri/target/release/bundle/nsis"; Filter = "*_${version}_*-setup.exe" }
    @{ Platform = "linux-x86_64"; Directory = "src-tauri/target/release/bundle/deb"; Filter = "*_${version}_*.deb" }
)
$artifacts = @()

foreach ($spec in $artifactSpecs) {
    if (-not (Test-Path $spec.Directory)) {
        continue
    }

    $matches = @(Get-ChildItem $spec.Directory -File -Filter $spec.Filter)
    if ($matches.Count -gt 1) {
        Write-Host "ERROR: more than one artifact for $($spec.Platform) in $($spec.Directory)" -ForegroundColor Red
        exit 1
    }
    if ($matches.Count -eq 0) {
        continue
    }

    $artifact = $matches[0]
    $sigPath = "$($artifact.FullName).sig"
    if (-not (Test-Path $sigPath)) {
        Write-Host "ERROR: $sigPath not found - build was not signed" -ForegroundColor Red
        Write-Host "Set TAURI_SIGNING_PRIVATE_KEY and TAURI_SIGNING_PRIVATE_KEY_PASSWORD" -ForegroundColor Yellow
        exit 1
    }

    $artifacts += @{
        Platform = $spec.Platform
        Path = $artifact.FullName
        Name = $artifact.Name
        SigPath = $sigPath
    }
}

if ($artifacts.Count -eq 0) {
    Write-Host "ERROR: no signed artifacts found for v$version" -ForegroundColor Red
    Write-Host "Run 'npm run tauri build' first with TAURI_SIGNING_PRIVATE_KEY configured" -ForegroundColor Yellow
    exit 1
}

foreach ($artifact in $artifacts) {
    Write-Host "Artifact ($($artifact.Platform)): $($artifact.Name)"
    Write-Host "Signature: $(Split-Path $artifact.SigPath -Leaf)"
}

# --- Release notes ---
if (-not $Notes) {
    $Notes = "Companion v$version"
}

# --- 1. GitHub release on slayner/ziggs (public) ---
Write-Host ""
Write-Host "[1/4] GitHub release..." -ForegroundColor Yellow
$tag = "v$version"
$repo = "slayner/ziggs"

# Replace an existing release with the same tag.
$existingTag = $null
try { $existingTag = gh release view $tag --repo $repo 2>&1 } catch {}
if ($LASTEXITCODE -eq 0 -and $existingTag) {
    Write-Host "  Release $tag already exists, replacing it..."
    gh release delete $tag --repo $repo --yes 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}

$releaseAssets = @()
foreach ($artifact in $artifacts) {
    $releaseAssets += $artifact.Path
    $releaseAssets += $artifact.SigPath
}

gh release create $tag $releaseAssets --repo $repo --title "v$version" --notes $Notes --latest 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: gh release create failed" -ForegroundColor Red
    exit 1
}
Write-Host "  Release created: https://github.com/slayner/ziggs/releases/tag/$tag"

# --- Update companion-release.json ---
Write-Host ""
Write-Host "[2/3] Updating manifest..." -ForegroundColor Yellow

$platforms = @{}
$downloads = @{}
foreach ($artifact in $artifacts) {
    $url = "https://github.com/slayner/ziggs/releases/download/v$version/$([uri]::EscapeDataString($artifact.Name))"
    $platforms[$artifact.Platform] = @{
        signature = (Get-Content $artifact.SigPath -Raw).Trim()
        url = $url
    }
    $downloads[$artifact.Platform] = @{ url = $url }
}

$manifest = @{
    version = $version
    notes = $Notes
    pub_date = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    platforms = $platforms
    downloads = $downloads
}

$manifestPath = "../backend/data/companion-release.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content $manifestPath -NoNewline
Write-Host "  $manifestPath updated"

Write-Host ""
Write-Host "[3/3] Publishing manifest to the VPS..." -ForegroundColor Yellow
scp -o IdentitiesOnly=yes -i $SshKey $manifestPath "${VpsHost}:/tmp/companion-release.json"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: manifest upload failed" -ForegroundColor Red
    exit 1
}
ssh -o IdentitiesOnly=yes -i $SshKey $VpsHost "install -o ziggs -g ziggs -m 0644 /tmp/companion-release.json $VpsManifestPath"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: manifest installation on the VPS failed" -ForegroundColor Red
    exit 1
}
$publishedVersion = (Invoke-RestMethod "https://ziggs.xyz/companion/latest.json").version
if ($publishedVersion -ne $version) {
    Write-Host "ERROR: production announced v$publishedVersion, expected v$version" -ForegroundColor Red
    exit 1
}
Write-Host "  Production announces v$publishedVersion"

Write-Host ""
Write-Host "=== Release v$version published! ===" -ForegroundColor Green
Write-Host "  GitHub: https://github.com/slayner/ziggs/releases/tag/$tag"
foreach ($artifact in $artifacts) {
    Write-Host "  Download ($($artifact.Platform)): https://github.com/slayner/ziggs/releases/download/v$version/$($artifact.Name)"
}
