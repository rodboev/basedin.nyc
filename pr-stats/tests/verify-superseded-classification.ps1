param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path
$match = [regex]::Match($content, 'var PR_DATA = (\[.*?\]);', [System.Text.RegularExpressions.RegexOptions]::Singleline)
if (-not $match.Success) {
    throw "Could not find PR_DATA in index.html."
}

$items = $match.Groups[1].Value | ConvertFrom-Json
$requiredNumbers = @(3563)
foreach ($number in $requiredNumbers) {
    $target = @($items | Where-Object { $_.number -eq $number })
    if ($target.Count -ne 1) {
        throw "Expected exactly one PR_DATA entry for #$number, found $($target.Count)."
    }

    if ($target[0].statusKey -ne "superseded") {
        throw "Expected #$number to be classified as superseded, found '$($target[0].statusKey)'."
    }
}

$optional1085 = @($items | Where-Object { $_.number -eq 1085 })
if ($optional1085.Count -gt 1) {
    throw "Expected at most one PR_DATA entry for #1085, found $($optional1085.Count)."
}
if ($optional1085.Count -eq 1 -and $optional1085[0].statusKey -ne "superseded") {
    throw "Expected #1085 to be classified as superseded when present, found '$($optional1085[0].statusKey)'."
}

Write-Host "#3563 is classified as superseded, and #1085 is superseded when present in the report window."
