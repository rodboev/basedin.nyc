param(
    [string]$CacheFile = "$PSScriptRoot\..\.pr-classification-cache.json"
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "..\generate.ps1") -CacheFile $CacheFile -VerifyWebuiCreditsOnly
