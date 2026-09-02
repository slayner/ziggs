#!/usr/bin/env powershell
<#
Publica arquivos do bot-v2 já validados na VPS de produção.

Uso:
  powershell -ExecutionPolicy Bypass -File deploy/publish-bot.ps1 cogs/massinfo_access.py cogs/general.py
#>

param(
    [Parameter(Mandatory, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Files
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$botRoot = (Resolve-Path (Join-Path $repoRoot "bot-v2")).Path
$sshKey = "$env:USERPROFILE\.ssh\hetzner_ziggs"
$prodHost = "root@167.233.241.191"
$remoteBot = "/home/ziggs/ziggs/bot-v2"
$tempDir = $null

if (-not (Test-Path -LiteralPath $sshKey -PathType Leaf)) {
    throw "Chave SSH não encontrada: $sshKey"
}

function Invoke-ProductionSsh([string]$Command) {
    & ssh -o IdentitiesOnly=yes -i $sshKey $prodHost $Command
    if ($LASTEXITCODE -ne 0) { throw "Comando remoto falhou: $Command" }
}

try {
    $tempDir = (Invoke-ProductionSsh "mktemp -d /tmp/ziggs-bot-deploy.XXXXXX").Trim()
    $botPrefix = $botRoot.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar

    foreach ($file in $Files) {
        if ([IO.Path]::IsPathRooted($file)) { throw "Use caminhos relativos a bot-v2/: $file" }
        $localPath = [IO.Path]::GetFullPath((Join-Path $botRoot $file))
        if (-not $localPath.StartsWith($botPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
            throw "Arquivo do bot não encontrado: $file"
        }

        $relativePath = $localPath.Substring($botPrefix.Length).Replace("\", "/")
        if ($relativePath.Contains("'")) { throw "Nome de arquivo não suportado: $file" }
        $remoteTempFile = "$tempDir/$([Guid]::NewGuid().ToString('N'))"
        & scp -o IdentitiesOnly=yes -i $sshKey $localPath "${prodHost}:$remoteTempFile"
        if ($LASTEXITCODE -ne 0) { throw "Upload falhou: $file" }
        Invoke-ProductionSsh "install -D -o ziggs -g ziggs -m 0644 '$remoteTempFile' '$remoteBot/$relativePath'"
        Write-Host "Publicado: bot-v2/$relativePath"
    }

    Invoke-ProductionSsh "systemctl restart ziggs-bot && systemctl is-active --quiet ziggs-bot"
    Write-Host "Bot reiniciado" -ForegroundColor Green
}
finally {
    if ($tempDir) { & ssh -o IdentitiesOnly=yes -i $sshKey $prodHost "rm -rf '$tempDir'" | Out-Null }
}