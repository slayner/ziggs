#!/usr/bin/env powershell
# companion/scripts/publish.ps1
# Publica os artefatos assinados do ultimo build em um release do GitHub:
#   1. GitHub release no slayner/ziggs (artefatos + assinaturas)
#   2. Atualiza companion-release.json com os artefatos descobertos
#
# Uso: cd companion ; powershell -ExecutionPolicy Bypass -File scripts/publish.ps1 [-Notes "texto"]
# Pre-requisito: build ja feito (npm run tauri build com signing key no env)

param(
    [string]$Notes = ""
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot/..

# --- Carrega config do companion ---
$tauriConf = Get-Content "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json
$version = $tauriConf.version

Write-Host "=== Publicando companion v$version ===" -ForegroundColor Cyan

# --- Acha os artefatos assinados do build (versao atual) ---
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
        Write-Host "ERRO: mais de um artefato para $($spec.Platform) em $($spec.Directory)" -ForegroundColor Red
        exit 1
    }
    if ($matches.Count -eq 0) {
        continue
    }

    $artifact = $matches[0]
    $sigPath = "$($artifact.FullName).sig"
    if (-not (Test-Path $sigPath)) {
        Write-Host "ERRO: $sigPath nao encontrado - build nao foi assinado" -ForegroundColor Red
        Write-Host "Configure TAURI_SIGNING_PRIVATE_KEY e TAURI_SIGNING_PRIVATE_KEY_PASSWORD" -ForegroundColor Yellow
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
    Write-Host "ERRO: nenhum artefato assinado encontrado para v$version" -ForegroundColor Red
    Write-Host "Rode 'npm run tauri build' primeiro (com TAURI_SIGNING_PRIVATE_KEY no env)" -ForegroundColor Yellow
    exit 1
}

foreach ($artifact in $artifacts) {
    Write-Host "Artefato ($($artifact.Platform)): $($artifact.Name)"
    Write-Host "Sig: $(Split-Path $artifact.SigPath -Leaf)"
}

# --- Notes do release ---
if (-not $Notes) {
    $Notes = "Companion v$version"
}

# --- 1. GitHub release on slayner/ziggs (public) ---
Write-Host ""
Write-Host "[1/4] GitHub release..." -ForegroundColor Yellow
$tag = "v$version"
$repo = "slayner/ziggs"

# Deleta release anterior se existir (mesma tag)
$existingTag = $null
try { $existingTag = gh release view $tag --repo $repo 2>&1 } catch {}
if ($LASTEXITCODE -eq 0 -and $existingTag) {
    Write-Host "  Release $tag ja existe, deletando..."
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
    Write-Host "ERRO: gh release create falhou" -ForegroundColor Red
    exit 1
}
Write-Host "  Release criado: https://github.com/slayner/ziggs/releases/tag/$tag"

# --- 2. Atualiza companion-release.json ---
Write-Host ""
Write-Host "[2/2] Atualizando manifest..." -ForegroundColor Yellow

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
Write-Host "  $manifestPath atualizado"

Write-Host ""
Write-Host "=== Release v$version preparado! ===" -ForegroundColor Green
Write-Host "  GitHub: https://github.com/slayner/ziggs/releases/tag/$tag"
foreach ($artifact in $artifacts) {
    Write-Host "  Download ($($artifact.Platform)): https://github.com/slayner/ziggs/releases/download/v$version/$($artifact.Name)"
}
