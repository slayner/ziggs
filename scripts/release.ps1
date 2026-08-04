#release.ps1 — builda o companion, cria tag, publica release no GitHub.
#Uso: .\scripts\release.ps1 [-Version 0.2.0] [-Notes "texto"]
#Sem -Version: patch automático (0.1.0 -> 0.1.1). Sem -Notes: changelog dos commits.
#Requer: gh CLI autenticado, rustup, node/npm.

param(
  [string]$Version,
  [string]$Notes
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\.."
$tauriConf = "$root\src-tauri\tauri.conf.json"
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:ProgramFiles\GitHub CLI;$env:PATH"

# Ler versão atual do tauri.conf.json
$conf = Get-Content $tauriConf -Raw | ConvertFrom-Json
$current = $conf.version
if (-not $Version) {
  $parts = $current.Split(".")
  $Version = "$($parts[0]).$($parts[1]).$([int]$parts[2] + 1)"
  Write-Host "Auto-bump: $current -> $Version"
}

if ($Version -eq $current) {
  Write-Host "Versao $Version ja existe no tauri.conf.json. Use -Version com numero maior." -ForegroundColor Yellow
  exit 1
}

# Atualizar tauri.conf.json
$conf.version = $Version
$conf | ConvertTo-Json -Depth 10 | Set-Content $tauriConf -Encoding UTF8
Write-Host "Versao atualizada para $Version no tauri.conf.json"

# Build com assinatura
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content "$env:USERPROFILE\.tauri\ziggs-companion.key" -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = Get-Content "$env:USERPROFILE\.tauri\ziggs-companion.key.pass"

Write-Host "Buildando companion..."
Push-Location $root
npm run tauri build 2>&1 | Select-Object -Last 5
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "Build falhou" -ForegroundColor Red; exit 1 }
Pop-Location

$exe = "$root\src-tauri\target\release\bundle\nsis\Ziggs Companion_${Version}_x64-setup.exe"
$sig = "$exe.sig"

if (-not (Test-Path $exe)) { Write-Host "Installer nao encontrado: $exe" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $sig)) { Write-Host "Assinatura .sig nao encontrada: $sig" -ForegroundColor Red; exit 1 }

# Commit da versao
git add $tauriConf
git commit -m "v$Version" 2>&1 | Out-Null
git push origin main 2>&1 | Out-Null

# Gerar changelog se nao veio Notes
if (-not $Notes) {
  $lastTag = git describe --tags --abbrev=0 2>$null
  if ($lastTag) {
    $Notes = git log "$lastTag..HEAD" --pretty=format:"- %s" 2>$null
  } else {
    $Notes = git log --pretty=format:"- %s" -10 2>$null
  }
  if (-not $Notes) { $Notes = "Release $Version" }
}

Write-Host "`nCriando release v$Version no GitHub..."
gh release create "v$Version" `
  -R slayner/ziggs `
  --title "v$Version" `
  --notes $Notes `
  "$exe" "$sig"

Write-Host "`nRelease v$Version publicada!" -ForegroundColor Green
Write-Host "  EXE: $(Split-Path $exe -Leaf)"
Write-Host "  SIG: $(Split-Path $sig -Leaf)"