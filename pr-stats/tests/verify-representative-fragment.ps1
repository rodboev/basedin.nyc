param(
    [string]$IndexPath = "$PSScriptRoot\..\index.html"
)

$indexContent = Get-Content -Raw -Path $IndexPath

if ($indexContent -notmatch '<h2>Representative PRs</h2>') {
    throw "Representative PRs are missing from index.html."
}

$representativeMatch = [regex]::Match(
    $indexContent,
    '<h2>Representative PRs</h2>\s*<table>.*?</table>',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $representativeMatch.Success) {
    throw "Could not isolate the representative PR block in index.html."
}

$representativeContent = $representativeMatch.Value

if ($indexContent -match 'id="representative-prs"' -or
    $indexContent -match 'loadRepresentativePrs\(\)' -or
    $indexContent -match 'fetch\(src, \{ credentials: ''same-origin'' \}\)') {
    throw "index.html still contains the representative PR fragment loader."
}

if ($representativeContent -notmatch 'https://github.com/nesquena/hermes-webui/pull/3571') {
    throw "Representative PRs are missing saved prompts feature PR #3571."
}

if ($representativeContent -notmatch 'https://github.com/NousResearch/hermes-agent/pull/40410') {
    throw "Representative PRs are missing agent PR #40410."
}

if ($representativeContent -notmatch 'https://github.com/NousResearch/hermes-agent/pull/39005') {
    throw "Representative PRs are missing agent PR #39005."
}

if ($representativeContent -match 'https://github.com/nesquena/hermes-webui/pull/3860') {
    throw "Representative PRs still link the saved prompts entry to release PR #3860 instead of original PR #3571."
}

if ($representativeContent -match 'https://github.com/NousResearch/hermes-agent/pull/39001' -or
    $representativeContent -match 'https://github.com/nesquena/hermes-webui/pull/3606' -or
    $representativeContent -match 'https://github.com/nesquena/hermes-webui/pull/3667') {
    throw "Representative PRs still include entries that should have been replaced in the curated examples list."
}

Write-Host "Representative PRs are inlined and include the curated set."
