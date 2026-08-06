#!/usr/bin/env pwsh
# companion/scripts/release.ps1
# Compila e publica um release do companion em um comando:
#   1. Bump version no tauri.conf.json
#   2. npm run tauri build (com signing)
#   3. publish.ps1 (GitHub release + VPS + manifest)
#
# Uso:
#   pwsh scripts/release.ps1                    # patch bump
#   pwsh scripts/release.ps1 -Kind minor        # minor bump
#   pwsh scripts/release.ps1 -Set 0.2.0         # versão específica
#   pwsh scripts/release.ps1 -Notes "Fix: X"    # notes do release

param(
    [ValidateSet("patch","minor","major")]
    [string]$Kind = "patch",
    [string]$Set = "",
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

# --- Verifica env vars de assinatura ---
if (!$env:TAURI_SIGNING_PRIVATE_KEY) {
    $keyPath = "$env:USERPROFILE\.tauri\ziggs-companion.key"
    if (Test-Path $keyPath) {
        $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $keyPath -Raw
    } else {
        Write-Host "ERRO: TAURI_SIGNING_PRIVATE_KEY não setada e $keyPath não existe" -ForegroundColor Red
        exit 1
    }
}

if (!$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
    Write-Host "TAURI_SIGNING_PRIVATE_KEY_PASSWORD não setada." -ForegroundColor Yellow
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = Read-Host "Digite a senha da signing key" -AsSecureString | ConvertFrom-SecureString -AsPlainText
}

# --- 1. Bump version ---
Write-Host "=== [1/3] Bump version ===" -ForegroundColor Cyan
& pwsh scripts/bump-version.ps1 -Kind $Kind -Set $Set
if ($LASTEXITCODE -ne 0) { exit 1 }

# --- 2. Build ---
Write-Host "`n=== [2/3] Build ===" -ForegroundColor Cyan
npm run tauri build 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: build falhou" -ForegroundColor Red
    exit 1
}

# --- 3. Publish ---
Write-Host "`n=== [3/3] Publish ===" -ForegroundColor Cyan
& pwsh scripts/publish.ps1 -Notes $Notes
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n=== Release completo! ===" -ForegroundColor Green