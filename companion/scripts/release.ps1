#!/usr/bin/env powershell
# companion/scripts/release.ps1
# Compila e publica um release do companion em um comando:
#   1. Bump version no tauri.conf.json
#   2. npm run tauri build (com signing)
#   3. publish.ps1 (GitHub release + VPS + manifest)
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts/release.ps1                    # patch bump
#   powershell -ExecutionPolicy Bypass -File scripts/release.ps1 -Kind minor         # minor bump
#   powershell -ExecutionPolicy Bypass -File scripts/release.ps1 -Set 0.2.0         # versao especifica
#   powershell -ExecutionPolicy Bypass -File scripts/release.ps1 -Notes "Fix: X"    # notes do release

param(
    [ValidateSet("patch","minor","major")]
    [string]$Kind = "patch",
    [string]$Set = "",
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

# --- Verifica env vars de assinatura ---
if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
    $keyPath = "$env:USERPROFILE\.tauri\ziggs-companion.key"
    if (Test-Path $keyPath) {
        $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $keyPath -Raw
    } else {
        Write-Host "ERRO: TAURI_SIGNING_PRIVATE_KEY nao setada e $keyPath nao existe" -ForegroundColor Red
        exit 1
    }
}

if (-not $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
    Write-Host "TAURI_SIGNING_PRIVATE_KEY_PASSWORD nao setada." -ForegroundColor Yellow
    $sec = Read-Host "Digite a senha da signing key" -AsSecureString
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

# --- 1. Bump version ---
Write-Host "=== [1/3] Bump version ===" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File scripts/bump-version.ps1 -Kind $Kind -Set $Set
if ($LASTEXITCODE -ne 0) { exit 1 }

# --- 2. Build ---
Write-Host ""
Write-Host "=== [2/3] Build ===" -ForegroundColor Cyan
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
npm run tauri build 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: build falhou" -ForegroundColor Red
    exit 1
}

# --- 3. Publish ---
Write-Host ""
Write-Host "=== [3/3] Publish ===" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File scripts/publish.ps1 -Notes $Notes
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "=== Release completo! ===" -ForegroundColor Green