#!/usr/bin/env powershell
# Gera localmente o .deb Linux assinado do Companion dentro do WSL.
# A senha é digitada no terminal WSL com entrada oculta e nunca passa pelo PowerShell.
# Não publica artefatos, não altera manifests e não acessa a VPS.
#
# Uso (na raiz do repositório):
#   powershell -ExecutionPolicy Bypass -File .\companion\scripts\build-linux.ps1

param(
    [string]$Distro = "Ubuntu",
    [string]$WslUser = "gabriel"
)

$ErrorActionPreference = "Stop"

$inheritedSigningVariables = @(
    "TAURI_SIGNING_PRIVATE_KEY",
    "TAURI_SIGNING_PRIVATE_KEY_PATH",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
    "TAURI_PRIVATE_KEY",
    "TAURI_PRIVATE_KEY_PATH",
    "TAURI_PRIVATE_KEY_PASSWORD",
    "TAURI_KEY_PASSWORD"
) | Where-Object { Test-Path "Env:$_" }
if ($inheritedSigningVariables) {
    throw "Remova as variáveis de assinatura herdadas antes de executar este script: $($inheritedSigningVariables -join ', ')"
}

$companionPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bashScriptPath = Join-Path $companionPath "scripts\build-linux.sh"
$keyPath = Join-Path $env:USERPROFILE ".tauri\ziggs-companion.key"

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL não está disponível neste Windows."
}
$availableDistros = @(& wsl.exe --list --quiet 2>$null | ForEach-Object {
    ($_ -replace "`0", "").Trim()
} | Where-Object { $_ })
if ($LASTEXITCODE -ne 0 -or $availableDistros -notcontains $Distro) {
    throw "Distribuição WSL não encontrada: $Distro"
}

if (-not (Test-Path -LiteralPath $bashScriptPath -PathType Leaf)) {
    throw "Script Linux não encontrado: $bashScriptPath"
}
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw "Chave privada Tauri não encontrada em $keyPath"
}

function ConvertTo-WslPath([string]$Path) {
    # wslpath aceita a forma C:/...; barras invertidas chegam truncadas quando
    # o processo é iniciado pelo ambiente MSYS/Git Bash.
    $normalizedPath = $Path -replace '\\', '/'
    $result = & wsl.exe -d $Distro -u $WslUser -- wslpath -a -u $normalizedPath
    if ($LASTEXITCODE -ne 0 -or -not $result) {
        throw "Não foi possível converter o caminho para o WSL: $Path"
    }
    return ($result | Select-Object -Last 1).Trim()
}

$wslCompanionPath = ConvertTo-WslPath $companionPath
$wslKeyPath = ConvertTo-WslPath $keyPath

Write-Host "=== Build Linux assinada do Ziggs Companion ===" -ForegroundColor Cyan
Write-Host "Distro WSL: $Distro | Usuário: $WslUser"
Write-Host "A senha será solicitada com entrada oculta diretamente no terminal WSL."
Write-Host "Nenhum artefato será publicado."
Write-Host ""

# Os únicos argumentos enviados ao WSL são caminhos não secretos. A senha não é
# lida, convertida, exportada ou registrada por PowerShell.
& wsl.exe -d $Distro -u $WslUser -- bash "$wslCompanionPath/scripts/build-linux.sh" $wslCompanionPath $wslKeyPath
if ($LASTEXITCODE -ne 0) {
    throw "A build Linux assinada falhou. O checkout Windows não foi usado como diretório de build."
}

$output = & wsl.exe -d $Distro -u $WslUser -- bash -c 'printf "%s" "$HOME/artifacts/ziggs-companion"'
if ($LASTEXITCODE -eq 0 -and $output) {
    $wslOutput = ($output | Select-Object -Last 1).Trim()
    $sharePath = "\\wsl$\$Distro" + ($wslOutput -replace '/', '\')
    Write-Host ""
    Write-Host "Build concluída. Artefatos Linux: $wslOutput" -ForegroundColor Green
    Write-Host "Acesso pelo Windows: $sharePath"
}
