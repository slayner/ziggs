#!/usr/bin/env powershell
# companion/scripts/publish.ps1
# Empacota o ultimo build do companion num release completo:
#   1. GitHub release no slayner/ziggs (publico, exe + sig)
#   2. Atualiza companion-release.json (manifest do auto-updater, URL aponta pro GitHub)
#   3. Commit + push no ziggs-site
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

# --- Acha os artefatos do build (versao atual) ---
$bundleDir = "src-tauri/target/release/bundle/nsis"
$exeName = Get-ChildItem $bundleDir -Filter "*_${version}_*-setup.exe" | Select-Object -First 1
if (-not $exeName) {
    Write-Host "ERRO: nenhum *-setup.exe em $bundleDir" -ForegroundColor Red
    Write-Host "Rode 'npm run tauri build' primeiro (com TAURI_SIGNING_PRIVATE_KEY no env)" -ForegroundColor Yellow
    exit 1
}
$exePath = $exeName.FullName
$sigPath = "$exePath.sig"
if (-not (Test-Path $sigPath)) {
    Write-Host "ERRO: $sigPath nao encontrado - build nao foi assinado" -ForegroundColor Red
    Write-Host "Configure TAURI_SIGNING_PRIVATE_KEY e TAURI_SIGNING_PRIVATE_KEY_PASSWORD" -ForegroundColor Yellow
    exit 1
}

Write-Host "Exe: $($exeName.Name)"
Write-Host "Sig: $(Split-Path $sigPath -Leaf)"

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

gh release create $tag $exePath $sigPath --repo $repo --title "v$version" --notes $Notes --latest 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: gh release create falhou" -ForegroundColor Red
    exit 1
}
Write-Host "  Release criado: https://github.com/slayner/ziggs/releases/tag/$tag"

# --- 2. Atualiza companion-release.json ---
Write-Host ""
Write-Host "[2/3] Atualizando manifest..." -ForegroundColor Yellow

# Le a assinatura (base64 do conteudo do .sig)
$sigContent = Get-Content $sigPath -Raw

# Nome do arquivo no release do GitHub
$exeUrlName = "Ziggs-Companion_${version}_x64-setup.exe"
$downloadUrl = "https://github.com/slayner/ziggs/releases/download/v$version/$exeUrlName"

$manifest = @{
    version = $version
    notes = $Notes
    pub_date = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    platforms = @{
        "windows-x86_64" = @{
            signature = $sigContent.Trim()
            url = $downloadUrl
        }
    }
}

$manifestPath = "../backend/data/companion-release.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content $manifestPath -NoNewline
Write-Host "  $manifestPath atualizado"

# Copia o manifest pra VPS de producao (backend data + static companion dir)
$sshKey = "$env:USERPROFILE\.ssh\hetzner_ziggs"
$prodHost = "root@167.233.241.191"
scp -i $sshKey $manifestPath "${prodHost}:/home/ziggs/ziggs/backend/data/companion-release.json"
ssh -i $sshKey $prodHost "cp /home/ziggs/ziggs/backend/data/companion-release.json /var/www/ziggs.xyz/companion/latest.json" 2>&1 | Out-Null
Write-Host "  Manifest copiado pra VPS de producao"

# --- 3. Commit + push no ziggs-site ---
Write-Host ""
Write-Host "[3/3] Commit no ziggs-site..." -ForegroundColor Yellow
Set-Location ..
git add backend/data/companion-release.json companion/src-tauri/tauri.conf.json
$commitMsg = "companion: release v$version"
git commit -m $commitMsg 2>&1 | Out-Null
git push origin master 2>&1 | Out-Null
Write-Host "  Commitado e pushed"

# --- Reinicia backend na VPS de producao pra servir o novo manifest ---
ssh -i $sshKey $prodHost "systemctl restart ziggs-backend" 2>&1 | Out-Null
Write-Host "  Backend reiniciado na producao"

Write-Host ""
Write-Host "=== Release v$version publicado! ===" -ForegroundColor Green
Write-Host "  GitHub:  https://github.com/slayner/ziggs/releases/tag/$tag"
Write-Host "  Download: $downloadUrl"
Write-Host "  Auto-updater ativo - companions instalados vao atualizar sozinhos"