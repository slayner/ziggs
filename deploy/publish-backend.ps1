#!/usr/bin/env powershell
<#
Publica arquivos do backend já validados na VPS de produção.

Uso:
  powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 app/api/routes/render.py
  powershell -ExecutionPolicy Bypass -File deploy/publish-backend.ps1 -Migrate app/models/foo.py alembic/versions/foo.py
#>

param(
    [Parameter(Mandatory, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Files,
    [switch]$Migrate
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendRoot = (Resolve-Path (Join-Path $repoRoot "backend")).Path
$sshKey = "$env:USERPROFILE\.ssh\hetzner_ziggs"
$prodHost = "root@167.233.241.191"
$remoteBackend = "/home/ziggs/ziggs/backend"
$tempDir = $null

if (-not (Test-Path -LiteralPath $sshKey -PathType Leaf)) {
    throw "Chave SSH não encontrada: $sshKey"
}

function Invoke-ProductionSsh([string]$Command) {
    & ssh -o IdentitiesOnly=yes -i $sshKey $prodHost $Command
    if ($LASTEXITCODE -ne 0) { throw "Comando remoto falhou: $Command" }
}

try {
    $tempDir = (Invoke-ProductionSsh "mktemp -d /tmp/ziggs-backend-deploy.XXXXXX").Trim()
    $backendPrefix = $backendRoot.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar

    foreach ($file in $Files) {
        if ([IO.Path]::IsPathRooted($file)) { throw "Use caminhos relativos a backend/: $file" }
        $localPath = [IO.Path]::GetFullPath((Join-Path $backendRoot $file))
        if (-not $localPath.StartsWith($backendPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
            throw "Arquivo do backend não encontrado: $file"
        }

        $relativePath = $localPath.Substring($backendPrefix.Length).Replace("\", "/")
        if ($relativePath.Contains("'")) { throw "Nome de arquivo não suportado: $file" }
        $remoteTempFile = "$tempDir/$([Guid]::NewGuid().ToString('N'))"
        & scp -o IdentitiesOnly=yes -i $sshKey $localPath "${prodHost}:$remoteTempFile"
        if ($LASTEXITCODE -ne 0) { throw "Upload falhou: $file" }
        Invoke-ProductionSsh "install -D -o ziggs -g ziggs -m 0644 '$remoteTempFile' '$remoteBackend/$relativePath'"
        Write-Host "Publicado: backend/$relativePath"
    }

    if ($Migrate) {
        Invoke-ProductionSsh "cd $remoteBackend && sudo -u ziggs venv/bin/alembic upgrade head"
    }

    Invoke-ProductionSsh "systemctl restart ziggs-backend && systemctl is-active --quiet ziggs-backend"
    $health = $null
    for ($attempt = 1; $attempt -le 15; $attempt++) {
        try {
            $health = (Invoke-ProductionSsh "curl -fsS http://127.0.0.1:8000/health 2>/dev/null").Trim()
            if ($health -match '"status"\s*:\s*"ok"') { break }
        }
        catch {
            if ($attempt -eq 15) { throw }
        }
        Start-Sleep -Seconds 1
    }
    if ($health -notmatch '"status"\s*:\s*"ok"') { throw "Healthcheck inesperado: $health" }
    Write-Host "Produção saudável: $health" -ForegroundColor Green
}
finally {
    if ($tempDir) { & ssh -o IdentitiesOnly=yes -i $sshKey $prodHost "rm -rf '$tempDir'" | Out-Null }
}
