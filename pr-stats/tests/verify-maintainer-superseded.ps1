param(
    [string]$GeneratePath = "$PSScriptRoot\..\generate.ps1"
)

$ErrorActionPreference = "Stop"

function Get-ProbeFunctions([string]$Path) {
    $lines = Get-Content -Path $Path
    $start = ($lines | Select-String -Pattern '^function Get-ReleaseTag' | Select-Object -First 1).LineNumber - 1
    $end = ($lines | Select-String -Pattern '^function Get-ContributorShippedCount' | Select-Object -First 1).LineNumber - 2
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
$ClassificationCacheVersion = 3
$script:PullRequestStateCache = @{}
$script:PullRequestCommitAuthorCache = @{}
$script:PullRequestEvidenceCache = @{}
$script:ClassificationCacheHits = 0
$script:ClassificationCache = @{
    version = $ClassificationCacheVersion
    entries = @{}
    prPullStates = @{}
    prAuthorsByNumber = @{}
}
$RepoLeaderboardConfig = @{
    "mem0ai/mem0" = @{
        MaintainerLogins = @("taranjeet", "deshraj", "kartik-mem0", "chaithanyak42", "prathameshagrawal", "agumpandey")
        IntegrationBots = @()
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

$raw = gh pr view 5508 --repo mem0ai/mem0 --json number,state,closedAt,mergedAt,title,url,author 2>$null
if (-not $raw) {
    throw "Could not load PR #5508 from mem0ai/mem0."
}

$pr = $raw | ConvertFrom-Json
$pullRequest = [pscustomobject]@{
    repo = "mem0ai/mem0"
    number = [int]$pr.number
    state = [string]$pr.state
    closedAt = $pr.closedAt
    mergedAt = $pr.mergedAt
    title = [string]$pr.title
    url = [string]$pr.url
    author = @{ login = [string]$pr.author.login }
}

$classification = Get-ClosedPullRequestClassification -PullRequest $pullRequest
if ($classification.Classification -ne "superseded") {
    throw "Expected mem0 #5508 to classify as superseded, found '$($classification.Classification)'."
}

Write-Host "mem0 #5508 classifies as superseded."
