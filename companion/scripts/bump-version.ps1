#!/usr/bin/env powershell
# companion/scripts/bump-version.ps1
# Incrementa o patch version no tauri.conf.json
# Uso: powershell -ExecutionPolicy Bypass -File scripts/bump-version.ps1 [-Kind patch|minor|major] [-Set "0.2.0"]

param(
    [ValidateSet("patch","minor","major")]
    [string]$Kind = "patch",
    [string]$Set = ""
)

$path = "$PSScriptRoot/../src-tauri/tauri.conf.json"
$conf = Get-Content $path -Raw | ConvertFrom-Json

if ($Set) {
    $conf.version = $Set
} else {
    $parts = $conf.version.Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    $patch = [int]$parts[2]
    switch ($Kind) {
        "patch" { $patch++ }
        "minor" { $minor++; $patch = 0 }
        "major" { $major++; $minor = 0; $patch = 0 }
    }
    $conf.version = "$major.$minor.$patch"
}

$conf | ConvertTo-Json -Depth 10 | ForEach-Object { [System.IO.File]::WriteAllText($path, $_, [System.Text.UTF8Encoding]::new($false)) }
Write-Host "Version bumped to: $($conf.version)"