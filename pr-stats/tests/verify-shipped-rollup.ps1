param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path

$shippedCardMatch = [regex]::Match(
    $content,
    '<div class="stat-card"><div class="number green">(\d+)</div><div class="label">Shipped</div></div>'
)
if (-not $shippedCardMatch.Success) {
    throw "Could not find shipped stat card."
}

$shippedPillMatch = [regex]::Match(
    $content,
    '<div class="sort-pill active" data-status="shipped">Shipped \((\d+)\)</div>'
)
if (-not $shippedPillMatch.Success) {
    throw "Could not find shipped filter pill."
}

$shippedCardCount = [int]$shippedCardMatch.Groups[1].Value
$shippedPillCount = [int]$shippedPillMatch.Groups[1].Value

if ($shippedCardCount -ne $shippedPillCount) {
    throw "Shipped stat card count $shippedCardCount does not match shipped pill count $shippedPillCount."
}

if ($content -match 'data-status="accepted-indirect"' -or
    $content -match '"key":"accepted-indirect"' -or
    $content -match '"statusKey":"accepted-indirect"') {
    throw "Accepted-indirect is still exposed separately instead of being rolled into shipped."
}

Write-Host "Shipped counts are rolled up consistently."
