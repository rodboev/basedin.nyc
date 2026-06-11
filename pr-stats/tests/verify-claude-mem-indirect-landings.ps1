param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path
$match = [regex]::Match($content, 'var PR_DATA = (\[.*?\]);', [System.Text.RegularExpressions.RegexOptions]::Singleline)
if (-not $match.Success) {
    throw "Could not find PR_DATA in index.html."
}

$items = $match.Groups[1].Value | ConvertFrom-Json
foreach ($number in @(2848, 2850, 2851, 2852)) {
    $target = @($items | Where-Object { $_.number -eq $number })
    if ($target.Count -ne 1) {
        throw "Expected exactly one PR_DATA entry for #$number, found $($target.Count)."
    }

    if ($target[0].statusKey -ne "shipped") {
        throw "Expected #$number to roll up under shipped, found '$($target[0].statusKey)'."
    }

    if ($target[0].releaseLabel -ne "indirect") {
        throw "Expected #$number to show indirect release label, found '$($target[0].releaseLabel)'."
    }

    if ($target[0].viaLabel -ne "#2862") {
        throw "Expected #$number to point to #2862, found '$($target[0].viaLabel)'."
    }
}

Write-Host "claude-mem batch-landed PRs roll up under shipped and keep their indirect landing marker."
