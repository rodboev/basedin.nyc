param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path

$match = [regex]::Match(
    $content,
    '<div class="stat-card"><div class="number green">(\d+)%</div><div class="label">Acceptance rate \((\d+) withdrawn, (\d+) superseded, (\d+) lost\)</div></div>'
)
if (-not $match.Success) {
    throw "Could not find acceptance rate stat card with the expected wording."
}

$rate = [int]$match.Groups[1].Value
$withdrawn = [int]$match.Groups[2].Value
$superseded = [int]$match.Groups[3].Value
$lost = [int]$match.Groups[4].Value

if ($withdrawn -lt 0 -or $superseded -lt 0 -or $lost -lt 0) {
    throw "Acceptance rate breakdown counts should not be negative."
}

if ($rate -lt 0 -or $rate -gt 100) {
    throw "Acceptance rate should be between 0 and 100, found $rate."
}

Write-Host "Acceptance rate card uses the shortened not-shipped wording."
