#!/usr/bin/env powershell
# companion/scripts/publish.ps1
# Publishes signed artifacts from the latest build to a GitHub release:
#   1. GitHub release on slayner/ziggs (artifacts and signatures)
#   2. Updates and publishes companion-release.json with the discovered artifacts
#
# Usage: cd companion ; powershell -ExecutionPolicy Bypass -File scripts/publish.ps1 [-Notes "text"]
# Prerequisite: build completed with the signing key available in the environment

param(
    [string]$Notes = "",
    [string]$LinuxArtifactDirectory = ""
)

$VpsHost = "root@167.233.241.191"
$SshKey = Join-Path $HOME ".ssh/hetzner_ziggs"
$VpsManifestPath = "/home/ziggs/ziggs/backend/data/companion-release.json"

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

function Assert-TauriSignatureEnvelope([string]$SignaturePath, [string]$ArtifactPath) {
    $encoded = (Get-Content -LiteralPath $SignaturePath -Raw).Trim()
    if (-not $encoded) {
        throw "ERROR: signature is empty: $SignaturePath"
    }
    try {
        $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
    } catch {
        throw "ERROR: signature is not valid Base64: $SignaturePath"
    }
    if (-not $decoded.StartsWith("untrusted comment: signature from tauri secret key`n")) {
        throw "ERROR: signature does not use the expected Tauri/Minisign envelope: $SignaturePath"
    }

    # Validate the exact artifact cryptographically with Minisign in WSL. The
    # updater `.sig` is Base64-wrapped, while Minisign expects its text envelope.
    $updaterPubkey = (Get-Content "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json).plugins.updater.pubkey
    try {
        $publicKey = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($updaterPubkey)).Trim()
        $publicKeyLines = @($publicKey -split "`r?`n")
        if ($publicKeyLines.Count -ne 2 -or -not $publicKeyLines[0].StartsWith("untrusted comment: minisign public key: ")) {
            throw "unexpected Minisign public-key envelope"
        }
        $publicKeyComment = $publicKeyLines[0]
        $publicKeyPayload = $publicKeyLines[1]
        $keyBytes = [Convert]::FromBase64String($publicKeyPayload)
    } catch {
        throw "ERROR: updater public key is invalid"
    }
    if ($keyBytes.Length -ne 42 -or [Text.Encoding]::ASCII.GetString($keyBytes[0..1]) -ne "Ed") {
        throw "ERROR: updater public key is invalid"
    }
    $keyIdBytes = [byte[]]$keyBytes[2..9]
    [Array]::Reverse($keyIdBytes)
    $keyId = (-join ($keyIdBytes | ForEach-Object { $_.ToString("x2") })).ToUpperInvariant()
    if ($publicKeyComment -ne "untrusted comment: minisign public key: $keyId") {
        throw "ERROR: updater public key identifier is invalid"
    }
    $distro = "Ubuntu"
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "ERROR: WSL Minisign verification is unavailable"
    }
    $normalizedArtifactPath = $ArtifactPath -replace '\\', '/'
    $wslArtifactPath = (& wsl.exe -d $distro -- wslpath -a -u $normalizedArtifactPath | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $wslArtifactPath) {
        throw "ERROR: could not resolve the artifact path inside WSL: $ArtifactPath"
    }
    $script = @"
set -euo pipefail
key=`$(mktemp)
signature=`$(mktemp)
trap 'rm -f "`$key" "`$signature"' EXIT
printf '%s\n%s\n' '$publicKeyComment' '$publicKeyPayload' > "`$key"
printf '%s' '$encoded' | base64 -d > "`$signature"
minisign -V -p "`$key" -x "`$signature" -m '$wslArtifactPath'
"@
    # Encode the short, generated verifier to preserve newlines and paths with
    # spaces across the Windows-to-WSL command boundary.
    $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
    $output = & wsl.exe -d $distro -- bash -lc "printf '%s' '$encodedScript' | base64 -d | bash" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "ERROR: signature verification failed for $(Split-Path $ArtifactPath -Leaf): $output"
    }
}

# --- Handle Linux artifact from custom directory ---
if ($LinuxArtifactDirectory) {
    $linuxSource = (Resolve-Path -LiteralPath $LinuxArtifactDirectory).Path
    $linuxTarget = Join-Path $PWD "src-tauri/target/release/bundle/deb"
    New-Item -ItemType Directory -Force -Path $linuxTarget | Out-Null

    $linuxDebs = @(Get-ChildItem -LiteralPath $linuxSource -File -Filter "*.deb")
    if ($linuxDebs.Count -ne 1) {
        throw "ERROR: expected exactly one Linux .deb in $linuxSource; found $($linuxDebs.Count)"
    }
    $linuxDeb = $linuxDebs[0]
    $linuxSignature = "$($linuxDeb.FullName).sig"
    if (-not (Test-Path -LiteralPath $linuxSignature -PathType Leaf) -or (Get-Item -LiteralPath $linuxSignature).Length -eq 0) {
        throw "ERROR: valid Linux signature not found for $($linuxDeb.Name)"
    }
    Assert-TauriSignatureEnvelope $linuxSignature $linuxDeb.FullName

    Copy-Item -LiteralPath $linuxDeb.FullName, $linuxSignature -Destination $linuxTarget -Force
}

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
        throw "ERROR: artifact directory for $($spec.Platform) not found: $($spec.Directory)"
    }

    $matches = @(Get-ChildItem $spec.Directory -File -Filter $spec.Filter)
    if ($matches.Count -ne 1) {
        throw "ERROR: expected exactly one artifact for $($spec.Platform) in $($spec.Directory); found $($matches.Count)"
    }

    $artifact = $matches[0]
    $sigPath = "$($artifact.FullName).sig"
    if (-not (Test-Path $sigPath -PathType Leaf) -or (Get-Item $sigPath).Length -eq 0) {
        throw "ERROR: valid signature not found for $($artifact.Name)"
    }
    Assert-TauriSignatureEnvelope $sigPath $artifact.FullName

    $artifacts += @{
        Platform = $spec.Platform
        Path = $artifact.FullName
        Name = $artifact.Name
        SigPath = $sigPath
    }
}

if ($artifacts.Count -ne $artifactSpecs.Count) {
    throw "ERROR: release requires all configured platform artifacts."
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

$releaseTarget = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $releaseTarget) {
    throw "ERROR: could not determine the release commit"
}
gh release create $tag $releaseAssets --repo $repo --target $releaseTarget --title "v$version" --notes $Notes --latest 2>&1
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
    $publishedName = [uri]::UnescapeDataString(([uri]$url).Segments[-1])
    if ($publishedName -cne $artifact.Name) {
        throw "URL do manifesto não corresponde ao artefato selecionado: '$publishedName' != '$($artifact.Name)'"
    }
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
