# Remove only the empty hermes-agent v4 entry so the next run rebuilds it once.
# Does NOT clear shippedCounts on other repos (that forces expensive re-classification).
param(
    [string]$CacheFile = "$PSScriptRoot\..\.pr-classification-cache.json"
)

$ErrorActionPreference = "Stop"
python (Join-Path $PSScriptRoot "prep-leaderboard-retry.py") $CacheFile
