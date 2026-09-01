# Deploy vps_scanner.py to all three tunnel VPS
$ErrorActionPreference = 'Stop'
$scanner = "backend\scripts\vps_scanner.py"
$key = "$env:USERPROFILE\.ssh\hetzner_ziggs"
$sshOpts = "-o", "IdentitiesOnly=yes", "-i", $key

$hosts = @(
    @{ip="173.199.116.252"; name="tunnel-new-york"},
    @{ip="95.179.145.72";   name="tunnel-amsterdan"},
    @{ip="45.32.110.2";     name="tunnel-singapura"}
)

foreach ($h in $hosts) {
    Write-Host "`n=== $($h.name) ($($h.ip)) ===" -ForegroundColor Cyan
    scp -o IdentitiesOnly=yes -i $key $scanner "root@$($h.ip):/tmp/vps_scanner_new.py"
    if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED for $($h.name)" -ForegroundColor Red; continue }
    $remote = @"
install -m 644 /tmp/vps_scanner_new.py /root/vps_scanner.py
rm -f /tmp/vps_scanner_new.py
rm -f /root/.scan-report-spool.json
sha256sum /root/vps_scanner.py
systemctl restart ziggs-scanner
sleep 2
systemctl is-active ziggs-scanner
journalctl -u ziggs-scanner -n 5 --no-pager
"@
    ssh -o IdentitiesOnly=yes -i $key "root@$($h.ip)" $remote
    if ($LASTEXITCODE -ne 0) { Write-Host "SSH FAILED for $($h.name)" -ForegroundColor Red }
}