param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path

$totalMatch = [regex]::Match(
    $content,
    '<div class="stat-card"><div class="number">(\d+)</div><div class="label">Total PRs</div></div>'
)
if (-not $totalMatch.Success) {
    throw "Could not find total PRs stat card."
}

$shippedMatch = [regex]::Match(
    $content,
    '<div class="stat-card"><div class="number green">(\d+)</div><div class="label">Shipped</div></div>'
)
if (-not $shippedMatch.Success) {
    throw "Could not find shipped stat card."
}

$openMatch = [regex]::Match(
    $content,
    '<div class="stat-card"><div class="number yellow">(\d+)</div><div class="label">Open</div></div>'
)
if (-not $openMatch.Success) {
    throw "Could not find open stat card."
}

$lostWithdrawnMatch = [regex]::Match(
    $content,
    '<div class="stat-card"><div class="number">(\d+)</div><div class="label">Lost/Withdrawn</div></div>'
)
if (-not $lostWithdrawnMatch.Success) {
    throw "Could not find Lost/Withdrawn stat card."
}

$total = [int]$totalMatch.Groups[1].Value
$shipped = [int]$shippedMatch.Groups[1].Value
$open = [int]$openMatch.Groups[1].Value
$lostWithdrawn = [int]$lostWithdrawnMatch.Groups[1].Value

if (($shipped + $open + $lostWithdrawn) -ne $total) {
    throw "Summary cards do not add up: shipped=$shipped open=$open lostWithdrawn=$lostWithdrawn total=$total."
}

Write-Host "Summary cards add up to the total."
