param(
    [string]$GeneratePath = "$PSScriptRoot\..\generate.ps1"
)

$ErrorActionPreference = "Stop"

function Get-ProbeFunctions([string]$Path) {
    $lines = Get-Content -Path $Path
    $start = ($lines | Select-String -Pattern '^function Test-MatchesAnyPattern' | Select-Object -First 1).LineNumber - 1
    $end = ($lines | Select-String -Pattern '^function Get-RecentRepoPullRequests' | Select-Object -First 1).LineNumber - 2
    $block = ($lines[$start..$end] -join "`n")
    $prefix = @'
$Author = "rodboev"
$shippedPatterns = @("shipped", "cherry-picked", "merged-via", "salvaged into")
$duplicatePatterns = @("duplicate")
$supersededPatterns = @("supersede", "consolidat")
$creditPatterns = @("co-author", "coauthor", "co-authored", "authorship", "attribution", "credited")
$withdrawnPattern = '(?i)\bwithdraw(?:ing|n)?\b'
$authorClosePattern = '(?i)\bclos(?:ing|ed|e)\b'
$mergedCarryForwardPattern = '(?i)\bmerge(?:d|s|ing)?(?:\s+to\s+main)?\b'
$MinSpeculativeReferencedPrNumber = 100
$script:PullRequestStateCache = @{}
$script:PullRequestCommitAuthorCache = @{}
$script:PullRequestEvidenceCache = @{}
$RepoLeaderboardConfig = @{
    "stablyai/orca" = @{
        MaintainerLogins = @("nwparker", "AmethystLiang", "Jinwoo-H", "brennanb2025", "tmchow")
        IntegrationBots = @("buf0-bot[bot]")
    }
}
function Invoke-Gh {
    param(
        [switch]$SuppressErrors,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GhArgs
    )
    if ($SuppressErrors) { & gh @GhArgs 2>$null } else { & gh @GhArgs }
}
'@
    return [scriptblock]::Create("$prefix`n$block")
}

$probe = Get-ProbeFunctions -Path $GeneratePath
. $probe

function Get-ProbePullRequest([string]$Repo, [int]$Number) {
    $raw = gh pr view $Number --repo $Repo --json number,state,mergedAt,title,url,author 2>$null
    if (-not $raw) { throw "Could not load PR #$Number from $Repo." }
    $pr = $raw | ConvertFrom-Json
    return [pscustomobject]@{
        repo = $Repo
        number = [int]$pr.number
        state = [string]$pr.state
        mergedAt = $pr.mergedAt
        title = [string]$pr.title
        url = [string]$pr.url
        author = @{ login = [string]$pr.author.login }
    }
}

function Test-ProbeClassification {
    param(
        [string]$Repo,
        [int]$Number,
        [string]$ExpectedClassification
    )

    $pullRequest = Get-ProbePullRequest -Repo $Repo -Number $Number
    $evidence = Get-PullRequestEvidence -Repo $Repo -Number $Number
    $comments = Get-NonBotCommentText -Evidence $evidence
    $isAuthorWithdrawn = Test-IsAuthorWithdrawnEvidence -PullRequest $pullRequest -Evidence $evidence
    $acceptedSibling = Get-ReferencedMergedPullRequest -Repo $Repo -OriginalPr $pullRequest -Evidence $evidence -Text $comments
    if (-not $acceptedSibling) {
        $acceptedSibling = Get-TimelineCreditedMergedPullRequest -Repo $Repo -OriginalPr $pullRequest -Evidence $evidence
    }

    $classification = "lost"
    if ($isAuthorWithdrawn) {
        $classification = "withdrawn"
    } elseif ($acceptedSibling) {
        $classification = "accepted-indirect"
    } elseif (-not $comments -or $comments.Trim().Length -eq 0) {
        $classification = "withdrawn"
    }

    if ($classification -ne $ExpectedClassification) {
        throw "Expected #$Number to classify as $ExpectedClassification, got $classification."
    }

    Write-Host "#$Number -> $classification"
}

Test-ProbeClassification -Repo "nesquena/hermes-webui" -Number 4383 -ExpectedClassification "withdrawn"
Test-ProbeClassification -Repo "nesquena/hermes-webui" -Number 4384 -ExpectedClassification "withdrawn"

$accepted4329 = Get-ProbePullRequest -Repo "nesquena/hermes-webui" -Number 4329
$evidence4329 = Get-PullRequestEvidence -Repo "nesquena/hermes-webui" -Number 4329
$sibling4332 = Get-PullRequestState -Repo "nesquena/hermes-webui" -Number 4332
if (-not (Test-IsCreditedMergedSibling -Repo "nesquena/hermes-webui" -OriginalPr $accepted4329 -MergedPr $sibling4332 -Evidence $evidence4329)) {
    throw "Expected release #4332 to still credit original #4329."
}
Write-Host "#4329 release credit via #4332 still holds."

$accepted40410 = Get-ProbePullRequest -Repo "NousResearch/hermes-agent" -Number 40410
$evidence40410 = Get-PullRequestEvidence -Repo "NousResearch/hermes-agent" -Number 40410
$sibling40573 = Get-PullRequestState -Repo "NousResearch/hermes-agent" -Number 40573
if (-not (Test-IsCreditedMergedSibling -Repo "NousResearch/hermes-agent" -OriginalPr $accepted40410 -MergedPr $sibling40573 -Evidence $evidence40410)) {
    throw "Expected release #40573 to still credit original #40410."
}
Write-Host "#40410 release credit via #40573 still holds."

$accepted6362 = Get-ProbePullRequest -Repo "stablyai/orca" -Number 6362
$evidence6362 = Get-PullRequestEvidence -Repo "stablyai/orca" -Number 6362
$sibling6574 = Get-PullRequestState -Repo "stablyai/orca" -Number 6574
if (-not (Test-IsCreditedMergedSibling -Repo "stablyai/orca" -OriginalPr $accepted6362 -MergedPr $sibling6574 -Evidence $evidence6362)) {
    throw "Expected maintainer-carried #6574 to still credit original #6362."
}
Write-Host "#6362 maintainer-carried credit via #6574 still holds."

Test-ProbeClassification -Repo "stablyai/orca" -Number 6362 -ExpectedClassification "accepted-indirect"

Write-Host "Author withdrawal and release credit probes passed."
