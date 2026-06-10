param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path
$match = [regex]::Match($content, 'var PR_DATA = (\[.*?\]);', [System.Text.RegularExpressions.RegexOptions]::Singleline)
if (-not $match.Success) {
    throw "Could not find PR_DATA in index.html."
}

$items = $match.Groups[1].Value | ConvertFrom-Json
$expectations = @(
    [pscustomobject]@{ number = 39391; statusKey = "lost" },
    [pscustomobject]@{ number = 40144; statusKey = "withdrawn" }
)

foreach ($expectation in $expectations) {
    $target = @($items | Where-Object { $_.number -eq $expectation.number })
    if ($target.Count -ne 1) {
        throw "Expected exactly one PR_DATA entry for #$($expectation.number), found $($target.Count)."
    }

    if ($target[0].statusKey -ne $expectation.statusKey) {
        throw "Expected #$($expectation.number) to be classified as $($expectation.statusKey), found '$($target[0].statusKey)'."
    }
}

Write-Host "#39391 and #40144 are classified correctly."
