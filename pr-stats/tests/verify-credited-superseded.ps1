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
$continuationPatterns = @("same credit", "same commit", "same change", "reopen")
$withdrawnPattern = '(?i)\bwithdraw(?:ing|n)?\b'
$authorClosePattern = '(?i)\bclos(?:ing|ed|e)\b'
$mergedCarryForwardPattern = '(?i)\bmerge(?:d|s|ing)?(?:\s+to\s+main)?\b'
$MinSpeculativeReferencedPrNumber = 100
$ClosedClassificationCacheTtlHours = 0
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
    "stablyai/orca" = @{
        MaintainerLogins = @("nwparker", "AmethystLiang", "Jinwoo-H", "brennanb2025", "tmchow")
        IntegrationBots = @("buf0-bot[bot]")
    }
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
function Get-ExistingClosedClassificationEntry([string]$Repo, [int]$Number) { return $null }
function Get-ClosedClassificationCacheTtlHours([object]$PullRequest, [string]$Classification, [string]$EvidenceKind, [datetime]$Now) { return 0 }
function Get-CachedClosedClassification([string]$Repo, [int]$Number, [datetime]$Now, [int]$TtlHours) { return $null }
function Set-CachedClosedClassification([string]$Repo, [int]$Number, [string]$Classification, [string]$Release, [string]$ViaLabel, [string]$ViaUrl, [string]$EvidenceKind) {}
'@
    return [scriptblock]::Create("$prefix`n$block")
}

$probe = Get-ProbeFunctions -Path $GeneratePath
. $probe

function Get-ProbePullRequest([string]$Repo, [int]$Number) {
    $raw = gh pr view $Number --repo $Repo --json number,state,closedAt,mergedAt,title,url,author 2>$null
    if (-not $raw) { throw "Could not load PR #$Number from $Repo." }
    $pr = $raw | ConvertFrom-Json
    return [pscustomobject]@{
        repo = $Repo
        number = [int]$pr.number
        state = [string]$pr.state
        closedAt = $pr.closedAt
        mergedAt = $pr.mergedAt
        title = [string]$pr.title
        url = [string]$pr.url
        author = @{ login = [string]$pr.author.login }
    }
}

$orca6362 = Get-ProbePullRequest -Repo "stablyai/orca" -Number 6362
$orcaClassification = Get-ClosedPullRequestClassification -PullRequest $orca6362
if ($orcaClassification.Classification -ne "accepted-indirect") {
    throw "Expected orca #6362 to classify as accepted-indirect, found '$($orcaClassification.Classification)'."
}
if ($orcaClassification.ViaLabel -ne "#6574") {
    throw "Expected orca #6362 viaLabel #6574, found '$($orcaClassification.ViaLabel)'."
}
if ($orcaClassification.ViaUrl -ne "https://github.com/stablyai/orca/pull/6574") {
    throw "Expected orca #6362 viaUrl to point to #6574, found '$($orcaClassification.ViaUrl)'."
}
Write-Host "orca #6362 -> accepted-indirect via #6574"

$mem05508 = Get-ProbePullRequest -Repo "mem0ai/mem0" -Number 5508
$mem0Classification = Get-ClosedPullRequestClassification -PullRequest $mem05508
if ($mem0Classification.Classification -ne "superseded") {
    throw "Expected mem0 #5508 to classify as superseded, found '$($mem0Classification.Classification)'."
}
Write-Host "mem0 #5508 -> superseded"
