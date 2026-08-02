# Assina o .exe/instalador via Microsoft Trusted Signing (Azure) — resolve o
# bloqueio do Smart App Control, que trata binário sem Authenticode como
# desconhecido e bloqueia por padrão (ver CLAUDE.md, seção Auto-updater).
#
# Sem as env vars abaixo, PULA a assinatura (exit 0) — build local/dev
# continua saindo sem assinar, exatamente como sempre saiu, até o cadastro no
# Azure Portal estar concluído (conta Trusted Signing + verificação de
# identidade + certificate profile + role "Trusted Signing Certificate
# Profile Signer"). Chamado pelo Tauri via `bundle.windows.signCommand` no
# tauri.conf.json, com %1 = caminho do binário.
param(
    [Parameter(Mandatory = $true)]
    [string]$BinaryPath
)

$endpoint = $env:ZIGGS_TRUSTED_SIGNING_ENDPOINT
$account  = $env:ZIGGS_TRUSTED_SIGNING_ACCOUNT
$profile  = $env:ZIGGS_TRUSTED_SIGNING_PROFILE
$dlib     = $env:ZIGGS_TRUSTED_SIGNING_DLIB

if (-not $endpoint -or -not $account -or -not $profile) {
    Write-Warning "Trusted Signing nao configurado (faltam ZIGGS_TRUSTED_SIGNING_ENDPOINT/ACCOUNT/PROFILE) - saindo SEM assinar: $BinaryPath"
    exit 0
}
if (-not $dlib) {
    Write-Error "ZIGGS_TRUSTED_SIGNING_DLIB nao setado (caminho do Azure.CodeSigning.Dlib.dll, do pacote NuGet Microsoft.Trusted.Signing.Client)."
    exit 1
}
if (-not (Get-Command signtool -ErrorAction SilentlyContinue)) {
    Write-Error "signtool.exe nao encontrado no PATH - instale o Windows SDK."
    exit 1
}

# metadata.json exigido pelo /dmdf do signtool — regerado a cada chamada, os
# 3 valores vem só do Azure Portal (conta/endpoint/profile), nada secreto
# aqui; a autenticação de verdade (service principal / az login) é lida pelo
# dlib via variáveis padrão do Azure (AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET
# ou sessão do `az login`), não por este script.
$metadataPath = Join-Path $env:TEMP "ziggs-trusted-signing-metadata.json"
[ordered]@{
    Endpoint               = $endpoint
    CodeSigningAccountName = $account
    CertificateProfileName = $profile
} | ConvertTo-Json | Set-Content -Path $metadataPath -Encoding utf8

# Timestamp server oficial da Microsoft pro Trusted Signing — sobrescrevivel
# só se a documentação atual pedir outro.
$timestampUrl = if ($env:ZIGGS_TRUSTED_SIGNING_TIMESTAMP_URL) { $env:ZIGGS_TRUSTED_SIGNING_TIMESTAMP_URL } else { "http://timestamp.acs.microsoft.com" }

# NOTA: confira os flags exatos contra a documentacao ATUAL da Microsoft pro
# Trusted Signing antes do primeiro uso real — o nome do dlib e a sintaxe do
# signtool ja mudaram de versao pra versao do pacote cliente.
& signtool sign /v /fd SHA256 /tr $timestampUrl /td SHA256 /dlib $dlib /dmdf $metadataPath $BinaryPath
exit $LASTEXITCODE
