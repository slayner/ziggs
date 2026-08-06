#!/usr/bin/env pwsh
# companion/scripts/publish.ps1
# Empacota o último build do companion num release completo:
#   1. GitHub release no slayner/ziggs (exe + sig)
#   2. Copia exe + sig pra VPS de produção
#   3. Atualiza companion-release.json no backend
#   4. Commit + push no ziggs-site
#
# Uso: cd companion && pwsh scripts/publish.ps1 [-Notes "texto"]
# Pré-requisito: build já feito (npm run tauri build com signing key no env)

param(
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

# --- Carrega config do companion ---
$tauriConf = Get-Content "src-tauri/tauri.conf.json" -Raw | ConvertFrom-Json
$version = $tauriConf.version
$productName = $tauriConf.productName

Write-Host "=== Publicando companion v$version ===" -ForegroundColor Cyan

# --- Acha os artefatos do build ---
$bundleDir = "src-tauri/target/release/bundle/nsis"
$exeName = Get-ChildItem $bundleDir -Filter "*-setup.exe" | Select-Object -First 1
if (!$exeName) {
    Write-Host "ERRO: nenhum *-setup.exe em $bundleDir" -ForegroundColor Red
    Write-Host "Rode 'npm run tauri build' primeiro (com TAURI_SIGNING_PRIVATE_KEY no env)" -ForegroundColor Yellow
    exit 1
}
$exePath = $exeName.FullName
$sigPath = "$exePath.sig"
if (!(Test-Path $sigPath)) {
    Write-Host "ERRO: $sigPath não encontrado — build não foi assinado" -ForegroundColor Red
    Write-Host "Configure TAURI_SIGNING_PRIVATE_KEY e TAURI_SIGNING_PRIVATE_KEY_PASSWORD" -ForegroundColor Yellow
    exit 1
}

Write-Host "Exe: $($exeName.Name)"
Write-Host "Sig: $(Split-Path $sigPath -Leaf)"

# --- Notes do release ---
if (!$Notes) {
    $Notes = "Companion v$version"
}

# --- 1. GitHub release no slayner/ziggs ---
Write-Host "`n[1/4] GitHub release..." -ForegroundColor Yellow
$tag = "v$version"

# Deleta release anterior se existir (mesma tag)
$existing = gh release view $tag --repo slayner/ziggs 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Release $tag já existe, deletando..."
    gh release delete $tag --repo slayner/ziggs --yes 2>&1 | Out-Null
    # Deleta a tag também
    git push origin --delete $tag --repo slayner/ziggs 2>$null
    Start-Sleep -Seconds 2
}

gh release create $tag $exePath $sigPath `
    --repo slayner/ziggs `
    --title "v$version" `
    --notes $Notes `
    --latest 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: gh release create falhou" -ForegroundColor Red
    exit 1
}
Write-Host "  Release criado: https://github.com/slayner/ziggs/releases/tag/$tag"

# --- 2. Copia exe + sig pra VPS de produção ---
Write-Host "`n[2/4] Upload pra VPS de produção..." -ForegroundColor Yellow
$sshKey = "$env:USERPROFILE\.ssh\hetzner_ziggs"
$prodHost = "root@167.233.241.191"
$remoteDir = "/var/www/ziggs.xyz/companion"

scp -i $sshKey $exePath "${prodHost}:$remoteDir/Ziggs-Companion_${version}_x64-setup.exe"
scp -i $sshKey $sigPath "${prodHost}:$remoteDir/Ziggs-Companion_${version}_x64-setup.exe.sig"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: scp falhou (VPS de produção)" -ForegroundColor Red
    exit 1
}
Write-Host "  Copiado pra $remoteDir/"

# --- 3. Atualiza companion-release.json ---
Write-Host "`n[3/4] Atualizando manifest..." -ForegroundColor Yellow

# Lê a assinatura (base64 do conteúdo do .sig)
$sigContent = Get-Content $sigPath -Raw

# Nome do arquivo na URL pública (hifens, não underscores — bater com o que já existe)
$exeUrlName = "Ziggs-Companion_${version}_x64-setup.exe"

$manifest = @{
    version = $version
    notes = $Notes
    pub_date = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    platforms = @{
        "windows-x86_64" = @{
            signature = $sigContent.Trim()
            url = "https://ziggs.xyz/companion/$exeUrlName"
        }
    }
}

$manifestPath = "backend/data/companion-release.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content $manifestPath -NoNewline
Write-Host "  $manifestPath atualizado"

# Copia o manifest pra VPS de produção também
scp -i $sshKey $manifestPath "${prodHost}:/home/ziggs/ziggs/backend/data/companion-release.json"
Write-Host "  Manifest copiado pra VPS de produção"

# --- 4. Commit + push no ziggs-site ---
Write-Host "`n[4/4] Commit no ziggs-site..." -ForegroundColor Yellow
Set-Location ..
git add $manifestPath companion/src-tauri/tauri.conf.json
$commitMsg = "companion: release v$version"
git commit -m $commitMsg 2>&1 | Out-Null
git push origin master 2>&1 | Out-Null
Write-Host "  Commitado e pushed"

# --- Reinicia backend na VPS de produção pra servir o novo manifest ---
ssh -i $sshKey $prodHost "systemctl restart ziggs-backend" 2>&1 | Out-Null
Write-Host "  Backend reiniciado na produção"

Write-Host "`n=== Release v$version publicado! ===" -ForegroundColor Green
Write-Host "  GitHub:  https://github.com/slayner/ziggs/releases/tag/$tag"
Write-Host "  Download: https://ziggs.xyz/companion/$exeUrlName"
Write-Host "  Auto-updater já ativo — companions instalados vão atualizar sozinhos"