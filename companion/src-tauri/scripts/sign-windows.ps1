# Assina o .exe/instalador via Microsoft Artifact Signing (Azure) — resolve o
# bloqueio do Smart App Control, que trata binário sem Authenticode como
# desconhecido e bloqueia por padrão (ver CLAUDE.md, seção Auto-updater).
#
# OBS: o serviço foi renomeado de "Trusted Signing" pra "Artifact Signing" em
# meados de 2025, mas o nome do resource provider (Microsoft.CodeSigning), o
# nome do dlib (Azure.CodeSigning.Dlib.dll) e o pacote NuGet
# (Microsoft.ArtifactSigning.Client) permanecem. As env vars ZIGGS_TRUSTED_*
# mantêm o nome antigo pra não quebrar builds já configurados.
#
# Sem as env vars abaixo, PULA a assinatura (exit 0) — build local/dev
# continua saindo sem assinar, exatamente como sempre saiu, até o cadastro no
# Azure Portal estar concluído (conta Artifact Signing + verificação de
# identidade + certificate profile + role "Artifact Signing Certificate
# Profile Signer"). Chamado pelo Tauri via `bundle.windows.signCommand` no
# tauri.conf.json, com %1 = caminho do binário.
#
# PREREQUISITOS (instalar uma única vez na máquina de build):
#   1. Windows SDK 10.0.2261.755+ (signtool.exe) — via Visual Studio Installer
#      ou winget install Microsoft.WindowsSDK.BuildTools
#   2. .NET 8.0 Runtime (x64) — https://dotnet.microsoft.com/download/dotnet/8.0
#   3. Microsoft Visual C++ Redistributable (já presente na maioria das máquinas)
#   4. Artifact Signing Client dlib:
#      - Mais fácil: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools
#        (instala tudo: dlib + dependências, registra no PATH)
#      - OU manual: baixar .zip de https://www.nuget.org/packages/Microsoft.ArtifactSigning.Client
#        e extrair; ZIGGS_TRUSTED_SIGNING_DLIB aponta pro bin/x64/Azure.CodeSigning.Dlib.dll
#   5. Autenticação: `az login` (service principal ou conta com role
#      "Artifact Signing Certificate Profile Signer" na conta). O dlib usa
#      DefaultAzureCredential — az CLI é o caminho mais simples.
#
# ENV VARS (setar antes de `npm run tauri build`):
#   ZIGGS_TRUSTED_SIGNING_ENDPOINT  — endpoint regional da conta, ex: https://eus.codesigning.azure.net
#   ZIGGS_TRUSTED_SIGNING_ACCOUNT   — nome da Artifact Signing account
#   ZIGGS_TRUSTED_SIGNING_PROFILE   — nome do certificate profile
#   ZIGGS_TRUSTED_SIGNING_DLIB      — caminho completo do Azure.CodeSigning.Dlib.dll (x64)
#   ZIGGS_TRUSTED_SIGNING_TIMESTAMP_URL — (opcional) default http://timestamp.acs.microsoft.com
#
# CURIOSIDADE CRÍTICA: certificados Artifact Signing têm validade de 3 DIAS.
# O timestamp server é obrigatório — sem ele, a assinatura expira em 72h e o
# binário volta a ser "não confiável" no Windows. O /tr + /td SHA256 no
# signtool garante isso (RFC 3161 timestamp).
param(
    [Parameter(Mandatory = $true)]
    [string]$BinaryPath
)

$endpoint = $env:ZIGGS_TRUSTED_SIGNING_ENDPOINT
$account  = $env:ZIGGS_TRUSTED_SIGNING_ACCOUNT
$profile  = $env:ZIGGS_TRUSTED_SIGNING_PROFILE
$dlib     = $env:ZIGGS_TRUSTED_SIGNING_DLIB

if (-not $endpoint -or -not $account -or -not $profile) {
    Write-Warning "Artifact Signing nao configurado (faltam ZIGGS_TRUSTED_SIGNING_ENDPOINT/ACCOUNT/PROFILE) - saindo SEM assinar: $BinaryPath"
    exit 0
}
if (-not $dlib) {
    Write-Error "ZIGGS_TRUSTED_SIGNING_DLIB nao setado (caminho do Azure.CodeSigning.Dlib.dll, do pacote NuGet Microsoft.ArtifactSigning.Client ou do winget Microsoft.Azure.ArtifactSigningClientTools)."
    exit 1
}
if (-not (Test-Path $dlib)) {
    Write-Error "Dlib nao encontrado em: $dlib"
    exit 1
}

# Busca signtool.exe — tenta PATH, depois locais comuns do Windows SDK.
# O SDK 10.0.2261.755+ é obrigatório (versões anteriores NÃO funcionam com o dlib).
$signtool = $null
if (Get-Command signtool -ErrorAction SilentlyContinue) {
    $signtool = "signtool"
} else {
    $sdkRoots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($root in $sdkRoots) {
        if (-not (Test-Path $root)) { continue }
        $candidates = Get-ChildItem -Path $root -Directory | Where-Object { $_.Name -like "10.0.*" } | Sort-Object Name -Descending
        foreach ($c in $candidates) {
            $exe = Join-Path $c.FullName "x64\signtool.exe"
            if (Test-Path $exe) { $signtool = $exe; break }
        }
        if ($signtool) { break }
    }
}
if (-not $signtool) {
    Write-Error "signtool.exe nao encontrado - instale o Windows SDK 10.0.2261.755+ (winget install Microsoft.WindowsSDK.BuildTools ou via Visual Studio Installer)."
    exit 1
}

# metadata.json exigido pelo /dmdf do signtool — regerado a cada chamada. Os 3
# valores vem do Azure Portal (conta/endpoint/profile). A autenticação de
# verdade (az login / service principal) é lida pelo dlib via
# DefaultAzureCredential — nada secreto aqui.
# Endpoint DEVE bater com a região onde a conta foi criada (ver tabela de
# endpoints regionais em learn.microsoft.com/azure/artifact-signing/quickstart).
# Endpoint errado = 403 Forbidden silencioso.
$metadataPath = Join-Path $env:TEMP "ziggs-trusted-signing-metadata.json"
[ordered]@{
    Endpoint               = $endpoint
    CodeSigningAccountName = $account
    CertificateProfileName = $profile
} | ConvertTo-Json | Set-Content -Path $metadataPath -Encoding utf8

# Timestamp server oficial da Microsoft pro Artifact Signing.
# CRÍTICO: certificados Artifact Signing têm validade de 3 DIAS — sem timestamp
# a assinatura expira em 72h e o binário perde a confiança do Windows.
$timestampUrl = if ($env:ZIGGS_TRUSTED_SIGNING_TIMESTAMP_URL) { $env:ZIGGS_TRUSTED_SIGNING_TIMESTAMP_URL } else { "http://timestamp.acs.microsoft.com" }

Write-Host "Assinando: $BinaryPath"
Write-Host "  signtool:  $signtool"
Write-Host "  dlib:      $dlib"
Write-Host "  endpoint:  $endpoint"
Write-Host "  account:   $account"
Write-Host "  profile:   $profile"

# Flags verificados contra a doc atual (learn.microsoft.com/azure/artifact-signing/how-to-signing-integrations):
#   /v       — verbose
#   /fd      — digest algorithm pro arquivo (SHA256)
#   /tr      — RFC 3161 timestamp server URL
#   /td      — digest algorithm pro timestamp (SHA256)
#   /dlib    — caminho do Azure.CodeSigning.Dlib.dll (MESMA arquitetura do signtool: x64)
#   /dmdf    — caminho do metadata.json
# NOTA: o dlib e o signtool PRECISAM ser da mesma arquitetura (x64+x64 ou x86+x86).
#       x64 é o padrão — se usar signtool x86, aponte o dlib pra bin/x86.
& $signtool sign /v /fd SHA256 /tr $timestampUrl /td SHA256 /dlib $dlib /dmdf $metadataPath $BinaryPath
exit $LASTEXITCODE
