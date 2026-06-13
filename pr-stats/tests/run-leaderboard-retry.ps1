$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$CacheFile = Join-Path $Root ".pr-classification-cache.json"
$LogFile = Join-Path $Root ".generate-retry.log"

while ($true) {
    $rate = gh api rate_limit | ConvertFrom-Json
    $remaining = [int]$rate.resources.graphql.remaining
    $resetAt = [DateTimeOffset]::FromUnixTimeSeconds([int64]$rate.resources.graphql.reset)
    Write-Host "GraphQL remaining: $remaining (resets $($resetAt.ToLocalTime().ToString('HH:mm:ss')))"
    if ($remaining -ge 500) { break }
    $delay = [math]::Max(30, [math]::Ceiling(($resetAt.AddSeconds(30) - [DateTimeOffset]::UtcNow).TotalSeconds))
    Write-Host "Waiting ${delay}s..."
    Start-Sleep -Seconds $delay
}

& (Join-Path $PSScriptRoot "prep-leaderboard-retry.ps1") -CacheFile $CacheFile
& (Join-Path $Root "generate.ps1") -CacheFile $CacheFile 2>&1 | Tee-Object -FilePath $LogFile
