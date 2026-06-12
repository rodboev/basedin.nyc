param(
    [string]$Author = "rodboev",
    [string[]]$Repos = @("nesquena/hermes-webui", "NousResearch/hermes-agent", "thedotmack/claude-mem"
        # "cline/cline", "continuedev/continue", "CopilotKit/CopilotKit",
        # "MemPalace/mempalace", "mastra-ai/mastra", "github/github-mcp-server",
        # "lsdefine/GenericAgent"
    ),
    [Nullable[datetime]]$StartDate = $null,
    [ValidateSet("Default", "All")][string]$Span = "All",
    [string]$ReadmeRepo = "rodboev/pr-sweep",
    [string]$ReadmePath = "C:\Users\Rod\.claude\skills\pr\README.md",
    [string]$OutFile = "$PSScriptRoot\index.html",
    [string]$CacheFile = "$PSScriptRoot\.pr-classification-cache.json",
    [int]$ClosedClassificationCacheTtlHours = 24 * 30,
    [int]$LeaderboardCacheTtlHours = 24 * 7,
    [int]$LeaderboardTop = 10,
    [switch]$RebuildCache,
    [switch]$RebuildClassifications,
    [switch]$RefreshLeaderboardCache
)

$script:GenerateStartedAt = Get-Date

$shippedPatterns = @("Shipped", "shipped", "cherry-picked", "merged-via", "Salvaged into", "salvaged into")
$acceptedPatterns = @()
$duplicatePatterns = @("Duplicate", "duplicate")
$supersededPatterns = @("Superseded by", "superseded by", "superseded", "consolidated", "Consolidating")
$lostPatterns = @()
$withdrawnPattern = '(?i)\bwithdraw(?:ing|n)?\b'
$DefaultLeaderboardVisible = 10
$LeaderboardMax = 50
$LeaderboardClassifyTop = 20
$LeaderboardRateWindowDays = 7
$MinSpeculativeReferencedPrNumber = 100
$LeaderboardCacheKeyVersion = "community-shipped-v3"
$ClassificationCacheVersion = 3
$DefaultReportStartDate = [datetime]"2026-06-02"
$GhInvokeTimeoutSeconds = 120
$GhInvokeSlowLogSeconds = 5

$RepoLeaderboardConfig = @{
    "nesquena/hermes-webui" = @{
        MaintainerLogins = @("nesquena")
        IntegrationBots = @("nesquena-hermes")
    }
    "NousResearch/hermes-agent" = @{
        MaintainerLogins = @("teknium1")
        IntegrationBots = @()
    }
    "thedotmack/claude-mem" = @{
        MaintainerLogins = @("thedotmack")
        IntegrationBots = @()
    }
}

$EasternTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
$script:RepoOwnerCache = @{}
$script:PullRequestStateCache = @{}
$script:PullRequestEvidenceCache = @{}
$script:ClassificationCache = @{
    version = $ClassificationCacheVersion
    entries = @{}
    leaderboards = @{}
}
$script:ClassificationCacheHits = 0
$script:LeaderboardCacheHits = 0

function Set-ProcessEnvironmentFromCurrent([System.Diagnostics.ProcessStartInfo]$ProcessInfo) {
    foreach ($entry in [Environment]::GetEnvironmentVariables([EnvironmentVariableTarget]::Process).GetEnumerator()) {
        $ProcessInfo.Environment[$entry.Key] = [string]$entry.Value
    }
    $ProcessInfo.Environment["GIT_TERMINAL_PROMPT"] = "0"
    $ProcessInfo.Environment["GH_NO_UPDATE_NOTIFIER"] = "1"
    $ProcessInfo.Environment["GCM_INTERACTIVE"] = "never"
}

function Get-GhCommandDisplayText([string]$ArgText, [int]$MaxLength = 160) {
    if ($ArgText.Length -le $MaxLength) {
        return $ArgText
    }
    return $ArgText.Substring(0, $MaxLength) + "..."
}

function Invoke-Gh {
    param(
        [switch]$SuppressErrors,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GhArgs
    )

    if ($env:OS -ne "Windows_NT") {
        $env:GIT_TERMINAL_PROMPT = "0"
        $env:GH_NO_UPDATE_NOTIFIER = "1"
        $env:GCM_INTERACTIVE = "never"
        if ($SuppressErrors) {
            & gh @GhArgs 2>$null
        } else {
            & gh @GhArgs
        }
        return
    }

    # gh is a console-subsystem binary; conhost --headless suppresses window flashes.
    # Redirect gh output to a temp file — piping conhost stdout wraps JSON at ~80 columns.
    # PowerShell splits comma-separated --json field lists into separate args; rejoin them.
    $normalizedArgs = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $GhArgs.Count; $i++) {
        $arg = $GhArgs[$i]
        if ($arg -eq "--json" -and ($i + 1) -lt $GhArgs.Count) {
            $jsonFields = [System.Collections.Generic.List[string]]::new()
            $i++
            while ($i -lt $GhArgs.Count -and $GhArgs[$i] -notmatch '^--') {
                $jsonFields.Add($GhArgs[$i])
                $i++
            }
            $normalizedArgs.Add("--json")
            $normalizedArgs.Add(($jsonFields -join ","))
            $i--
            continue
        }
        $normalizedArgs.Add($arg)
    }

    $argText = ($normalizedArgs | ForEach-Object {
        if ($_ -match '[\s",]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $commandLabel = "gh $(Get-GhCommandDisplayText -ArgText $argText)"

    $stdoutFile = [IO.Path]::GetTempFileName()
    $stderrFile = "$stdoutFile.err"
    $inner = "gh $argText 1>`"$stdoutFile`" 2>`"$stderrFile`""
    $psi = [System.Diagnostics.ProcessStartInfo]::new('conhost.exe')
    $psi.Arguments = "--headless -- cmd /c $inner"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    Set-ProcessEnvironmentFromCurrent -ProcessInfo $psi
    $p = [System.Diagnostics.Process]::Start($psi)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $timedOut = -not $p.WaitForExit($GhInvokeTimeoutSeconds * 1000)
    $sw.Stop()

    if ($timedOut) {
        Write-ProgressHost "  gh timed out after ${GhInvokeTimeoutSeconds}s: $commandLabel" -ForegroundColor Red
        try {
            if (-not $p.HasExited) {
                $p.Kill($true)
            }
        } catch {}
        $global:LASTEXITCODE = 124
        Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
        return ""
    }

    $global:LASTEXITCODE = $p.ExitCode
    if ($sw.Elapsed.TotalSeconds -ge $GhInvokeSlowLogSeconds) {
        Write-ProgressHost "  gh slow ($([math]::Round($sw.Elapsed.TotalSeconds, 1))s): $commandLabel" -ForegroundColor Yellow
    }

    $stderr = if (Test-Path $stderrFile) { [IO.File]::ReadAllText($stderrFile) } else { "" }
    $stdout = if (Test-Path $stdoutFile) { [IO.File]::ReadAllText($stdoutFile) } else { "" }
    Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue

    if ($stderr -and -not $SuppressErrors) {
        [Console]::Error.Write($stderr)
    }

    $stdout.TrimEnd()
}

function Write-ProgressHost {
    param(
        [string]$Message,
        [ConsoleColor]$ForegroundColor = [ConsoleColor]::DarkGray,
        [switch]$NoNewline
    )

    if ($NoNewline) {
        Write-Host $Message -ForegroundColor $ForegroundColor -NoNewline
    } else {
        Write-Host $Message -ForegroundColor $ForegroundColor
    }
    [Console]::Out.Flush()
}

function Format-EasternDate([string]$IsoDate) {
    if (-not $IsoDate) { return "" }
    try {
        $utc = [datetime]::Parse($IsoDate, $null, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
        $eastern = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc.ToUniversalTime(), $EasternTimeZone)
        return $eastern.ToString("M/d/yy h:mm tt")
    } catch {
        return ""
    }
}

function Get-PullRequestEffectiveIsoDate([object]$PullRequest, [string]$StatusKey) {
    if ($StatusKey -eq "open") {
        return $PullRequest.createdAt
    }
    if ($PullRequest.closedAt) {
        return $PullRequest.closedAt
    }
    return $PullRequest.createdAt
}

function Get-ReleaseTag([string]$Text) {
    if (-not $Text) { return "" }
    if ($Text -match "v\d+\.\d+\.\d+") { return $Matches[0] }
    return ""
}

function Test-IsReleaseTitle([string]$Text) {
    return [bool](Get-ReleaseTag $Text)
}

function Get-RepoLabel([string]$Repo) {
    $repoShort = ($Repo -split '/')[-1]
    switch ($repoShort) {
        "hermes-webui" { return "webui" }
        "hermes-agent" { return "agent" }
        "github-mcp-server" { return "gh-mcp" }
        "GenericAgent" { return "generic-agent" }
        default { return $repoShort }
    }
}

function Get-RepoOwnerLogin([string]$Repo) {
    if (-not $script:RepoOwnerCache.ContainsKey($Repo)) {
        $owner = ""
        try {
            $owner = Invoke-Gh api "repos/$Repo" -q ".owner.login" 2>$null
        } catch {}
        $script:RepoOwnerCache[$Repo] = [string]$owner
    }
    return $script:RepoOwnerCache[$Repo]
}

function Test-IsLeaderboardBot([string]$Login) {
    if (-not $Login) { return $true }
    if ($Login -like "app/*") { return $true }
    if ($Login -eq "dependabot[bot]") { return $true }
    return $false
}

function Get-RepoLeaderboardExclusions([string]$Repo) {
    $owner = Get-RepoOwnerLogin -Repo $Repo
    $config = $RepoLeaderboardConfig[$Repo]
    $maintainers = @()
    $bots = @()
    if ($config) {
        if ($config.MaintainerLogins) { $maintainers = @($config.MaintainerLogins) }
        if ($config.IntegrationBots) { $bots = @($config.IntegrationBots) }
    }
    $all = @($owner) + $maintainers + $bots | Where-Object { $_ } | Select-Object -Unique
    return @{
        Owner = $owner
        Maintainers = @($maintainers | Where-Object { $_ })
        IntegrationBots = @($bots | Where-Object { $_ })
        All = @($all)
    }
}

function Test-IsLeaderboardExcludedLogin([string]$Login, [hashtable]$Exclusions) {
    if (-not $Login) { return $true }
    if (Test-IsLeaderboardBot -Login $Login) { return $true }
    if ($Exclusions.All -contains $Login) { return $true }
    return $false
}

function Get-LeaderboardCacheKey([string]$Repo, [object]$StartDate) {
    return "$Repo|$LeaderboardCacheKeyVersion|$(Get-StartDateCacheKey -Date $StartDate)"
}

function Get-ReportStartLabel([datetime]$Date) {
    return $Date.ToString("MMMM d, yyyy")
}

function Get-OptionalDateValue([object]$Date) {
    if ($null -eq $Date) { return $null }
    try {
        return [datetime]$Date
    } catch {
        return $null
    }
}

function Get-StartDateCacheKey([object]$Date) {
    $dateValue = Get-OptionalDateValue -Date $Date
    if ($null -ne $dateValue) {
        return $dateValue.ToString("yyyy-MM-dd")
    }
    return "all"
}

function Select-BestCrossReference {
    param(
        [object[]]$Candidates,
        [string]$ClosedAt
    )

    if ($Candidates.Count -eq 1 -and $Candidates[0] -is [System.Array]) {
        $Candidates = @($Candidates[0])
    }
    if (-not $Candidates -or $Candidates.Count -eq 0) { return $null }
    if (-not $ClosedAt) { return ($Candidates | Select-Object -Last 1).source }

    try {
        $closedAtDate = [datetime]$ClosedAt
    } catch {
        return ($Candidates | Select-Object -Last 1).source
    }

    return ($Candidates |
        Sort-Object @{
            Expression = {
                try {
                    [math]::Abs(($closedAtDate - [datetime]$_.createdAt).TotalSeconds)
                } catch {
                    [double]::PositiveInfinity
                }
            }
        } |
        Select-Object -First 1).source
}

function Get-PullRequestEvidence([string]$Repo, [int]$Number) {
    $cacheKey = "$Repo#$Number"
    if ($script:PullRequestEvidenceCache.ContainsKey($cacheKey)) {
        return $script:PullRequestEvidenceCache[$cacheKey]
    }

    $commentResult = @()
    try {
        $commentResult = @(Invoke-Gh api "repos/$Repo/issues/$Number/comments?per_page=100" 2>$null | ConvertFrom-Json)
    } catch {}

    $timelineResult = @()
    try {
        $timelineResult = @(Invoke-Gh api "repos/$Repo/issues/$Number/timeline?per_page=100" -H "Accept: application/vnd.github+json" 2>$null | ConvertFrom-Json)
    } catch {}

    $commentNodes = foreach ($comment in $commentResult) {
        [pscustomobject]@{
            body = $comment.body
            author = [pscustomobject]@{
                login = $comment.user.login
            }
            authorAssociation = $comment.author_association
        }
    }

    $timelineNodes = foreach ($event in $timelineResult) {
        if ($event.event -eq "cross-referenced" -and $event.source -and $event.source.issue -and $event.source.issue.pull_request) {
            [pscustomobject]@{
                __typename = "CrossReferencedEvent"
                createdAt = $event.created_at
                source = [pscustomobject]@{
                    __typename = "PullRequest"
                    number = $event.source.issue.number
                    title = $event.source.issue.title
                    state = if ($event.source.issue.pull_request.merged_at) { "MERGED" } else { "CLOSED" }
                    merged = [bool]$event.source.issue.pull_request.merged_at
                    mergedAt = $event.source.issue.pull_request.merged_at
                    url = $event.source.issue.html_url
                }
            }
        } elseif ($event.event -eq "referenced" -and $event.commit_id) {
            [pscustomobject]@{
                __typename = "ReferencedEvent"
                createdAt = $event.created_at
                commit = [pscustomobject]@{
                    oid = $event.commit_id
                    messageHeadline = ""
                    url = $event.commit_url
                }
            }
        } elseif ($event.event -eq "closed") {
            [pscustomobject]@{
                __typename = "ClosedEvent"
                createdAt = $event.created_at
                closer = [pscustomobject]@{
                    __typename = ""
                }
            }
        }
    }

    $evidence = [pscustomobject]@{
        comments = [pscustomobject]@{ nodes = @($commentNodes) }
        timelineItems = [pscustomobject]@{ nodes = @($timelineNodes | Where-Object { $_ }) }
    }

    $script:PullRequestEvidenceCache[$cacheKey] = $evidence
    return $evidence
}

function Get-ScalarValue([object]$Value) {
    if ($null -eq $Value) { return "" }
    if ($Value -is [System.Array]) { return Get-ScalarValue ($Value | Select-Object -First 1) }
    return $Value
}

function Get-PullRequestState([string]$Repo, [int]$Number, [switch]$Quiet) {
    $cacheKey = "$Repo#$Number"
    if ($script:PullRequestStateCache.ContainsKey($cacheKey)) {
        return $script:PullRequestStateCache[$cacheKey]
    }
    $ghParams = @{
        SuppressErrors = [bool]$Quiet
        GhArgs = @('pr', 'view', "$Number", '--repo', $Repo, '--json', 'number,state,mergedAt,title,url,author,body')
    }
    $raw = Invoke-Gh @ghParams 2>$null
    $result = $null
    if ($raw) {
        try { $result = $raw | ConvertFrom-Json } catch {}
    }
    $script:PullRequestStateCache[$cacheKey] = $result
    return $result
}

function Test-IsExplicitPullRequestReference([string]$Text, [int]$Number) {
    if (-not $Text) { return $false }
    return [bool]([regex]::Match($Text, "github\.com/[^/\s]+/[^/\s]+/pull/$Number\b"))
}

function Test-ShouldResolveReferencedPullRequest([string]$Text, [int]$Number) {
    if ($Number -ge $MinSpeculativeReferencedPrNumber) { return $true }
    return (Test-IsExplicitPullRequestReference -Text $Text -Number $Number)
}

function Get-NonBotCommentText([object]$Evidence) {
    return (@($Evidence.comments.nodes) |
        Where-Object { $_.author.login -ne "greptile-apps" } |
        ForEach-Object { $_.body }) -join "`n---`n"
}

function Test-IsAuthorWithdrawnEvidence([object]$PullRequest, [object]$Evidence) {
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }
    if (-not $authorLogin) { return $false }

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($comment.author.login -ne $authorLogin) { continue }
        if ([string]$comment.body -match $withdrawnPattern) {
            return $true
        }
    }

    return $false
}

function Test-IsSupersededEvidence([object]$PullRequest, [object]$Evidence) {
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }
    $maintainerAssociations = @("OWNER", "COLLABORATOR", "MEMBER")

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($authorLogin -and $comment.author.login -eq $authorLogin) { continue }
        if ($comment.authorAssociation -and $comment.authorAssociation -notin $maintainerAssociations) { continue }
        foreach ($pattern in $supersededPatterns) {
            if ([string]$comment.body -match [regex]::Escape($pattern)) {
                return $true
            }
        }
    }

    return $false
}

function Test-HasSupersededReference([object]$Evidence) {
    foreach ($comment in @($Evidence.comments.nodes)) {
        foreach ($pattern in $supersededPatterns) {
            if ([string]$comment.body -match [regex]::Escape($pattern)) {
                return $true
            }
        }
    }

    return $false
}

function Get-PullRequestReferenceText([string]$Repo, [int]$Number) {
    $details = Get-PullRequestState -Repo $Repo -Number $Number -Quiet
    if (-not $details) { return "" }

    $evidence = Get-PullRequestEvidence -Repo $Repo -Number $Number
    return @(
        (Get-ScalarValue $details.title),
        (Get-ScalarValue $details.body),
        (Get-NonBotCommentText -Evidence $evidence)
    ) -join "`n---`n"
}

function Test-IsCreditedMergedSibling([string]$Repo, [object]$OriginalPr, [object]$MergedPr) {
    if (-not $MergedPr) { return $false }

    $originalAuthor = Get-ScalarValue $OriginalPr.author.login
    if (-not $originalAuthor) { $originalAuthor = $Author }
    $mergedAuthor = Get-ScalarValue $MergedPr.author.login
    if ($originalAuthor -and $mergedAuthor -and $originalAuthor -eq $mergedAuthor) {
        return $true
    }

    $referenceText = Get-PullRequestReferenceText -Repo $Repo -Number $MergedPr.number
    if (-not $referenceText) { return $false }

    $referenceNeedles = @(
        "#$($OriginalPr.number)",
        "https://github.com/$Repo/pull/$($OriginalPr.number)",
        $(if ($originalAuthor) { "@$originalAuthor" } else { "" })
    ) | Where-Object { $_ }

    foreach ($needle in $referenceNeedles) {
        if ($referenceText -match [regex]::Escape($needle)) {
            return $true
        }
    }

    return $false
}

function Get-ReferencedMergedPullRequest([string]$Repo, [object]$OriginalPr, [string]$Text) {
    if (-not $Text) { return $null }
    $matches = [regex]::Matches($Text, '#(\d+)')
    $seen = @{}
    foreach ($match in $matches) {
        $num = [int]$match.Groups[1].Value
        if ($seen.ContainsKey($num)) { continue }
        $seen[$num] = $true
        if (-not (Test-ShouldResolveReferencedPullRequest -Text $Text -Number $num)) { continue }
        $pr = Get-PullRequestState -Repo $Repo -Number $num -Quiet
        if ($pr -and ($pr.state -eq "MERGED" -or $pr.mergedAt) -and (Test-IsCreditedMergedSibling -Repo $Repo -OriginalPr $OriginalPr -MergedPr $pr)) {
            return $pr
        }
    }
    return $null
}

function Get-TimelineCreditedMergedPullRequest([string]$Repo, [object]$OriginalPr, [object]$Evidence) {
    foreach ($node in @($Evidence.timelineItems.nodes)) {
        if ($node.__typename -ne "CrossReferencedEvent") { continue }
        if (-not $node.source -or $node.source.__typename -ne "PullRequest") { continue }
        if (-not $node.source.merged -and -not $node.source.mergedAt) { continue }

        $pr = Get-PullRequestState -Repo $Repo -Number $node.source.number -Quiet
        if ($pr -and (Test-IsCreditedMergedSibling -Repo $Repo -OriginalPr $OriginalPr -MergedPr $pr)) {
            return $pr
        }
    }

    return $null
}

function Get-RecentRepoPullRequests([string]$Repo, [int]$Limit = 500) {
    $raw = Invoke-Gh pr list --repo $Repo --state all --limit $Limit --json 'author,createdAt,state' 2>$null
    if (-not $raw) { return @() }
    return @(($raw | ConvertFrom-Json) | ForEach-Object { $_ })
}

function Get-PropertyIssueCount([object]$Data, [string]$Name) {
    $prop = $Data.PSObject.Properties[$Name]
    if (-not $prop -or $null -eq $prop.Value) { return $null }
    return $prop.Value.issueCount
}

function Get-LegacyLeaderboardStat(
    [string]$Repo,
    [string]$Login,
    [datetime]$RateSinceDate,
    [datetime]$Now,
    [double]$RateWindowDays
) {
    $aRaw = Invoke-Gh pr list --repo $Repo --author $Login --state all --limit 500 --json 'number,createdAt,state' 2>$null
    if (-not $aRaw) { return $null }
    $prs = @(($aRaw | ConvertFrom-Json) | ForEach-Object { $_ })
    if ($prs.Count -eq 0) { return $null }

    $dates = @()
    foreach ($pr in $prs) {
        if ($pr.createdAt) {
            try { $dates += [datetime]$pr.createdAt } catch {}
        }
    }
    $dates = @($dates | Sort-Object)

    $openCount = @($prs | Where-Object { $_.state -eq "OPEN" }).Count
    $credited = @($prs | Where-Object { $_.state -ne "OPEN" }).Count

    $recentCount = 0
    foreach ($pr in $prs) {
        if ($pr.createdAt) {
            try {
                if ([datetime]$pr.createdAt -gt $RateSinceDate) { $recentCount++ }
            } catch {}
        }
    }

    $rate = if ($RateWindowDays -gt 0) { [math]::Round($recentCount / $RateWindowDays, 1) } else { 0 }
    $last = if ($dates.Count -gt 0) { $dates[-1] } else { $null }
    $idle = if ($last) { [math]::Round(($Now - $last).TotalDays, 1) } else { 999 }
    $span = if ($dates.Count -ge 2) { ($dates[-1] - $dates[0]).TotalDays } else { 0 }

    return @{
        credited = $credited
        open = $openCount
        total = $prs.Count
        recentCount = $recentCount
        rate = $rate
        idle = $idle
        lastCreatedAt = if ($last) { $last.ToString("o") } else { "" }
        span = $span
    }
}

function Get-LeaderboardStats(
    [string]$Repo,
    [string[]]$Logins,
    [object[]]$RecentRepoPRs,
    [datetime]$RateSinceDate,
    [datetime]$Now,
    [double]$RateWindowDays
) {
    $stats = @{}
    $recentDatesByLogin = @{}

    foreach ($repoPr in $RecentRepoPRs) {
        $login = $repoPr.author.login
        if (-not $login) { continue }
        if (-not $recentDatesByLogin.ContainsKey($login)) {
            $recentDatesByLogin[$login] = @()
        }
        if ($repoPr.createdAt) {
            try { $recentDatesByLogin[$login] += [datetime]$repoPr.createdAt } catch {}
        }
    }

    $authorList = @($Logins | Where-Object { $_ } | Select-Object -Unique)
    if ($authorList.Count -eq 0) { return $stats }

    $batchSize = 20
    $rateSinceQualifier = $RateSinceDate.ToString("yyyy-MM-dd")

    for ($offset = 0; $offset -lt $authorList.Count; $offset += $batchSize) {
        $batch = @($authorList | Select-Object -Skip $offset -First $batchSize)
        $queryLines = @("query {")
        for ($idx = 0; $idx -lt $batch.Count; $idx++) {
            $login = $batch[$idx]
            $aliasBase = "a$($offset + $idx)"
            $queryLines += "  ${aliasBase}_total: search(query: `"repo:$Repo is:pr author:$login`", type: ISSUE, first: 1) { issueCount }"
            $queryLines += "  ${aliasBase}_open: search(query: `"repo:$Repo is:pr author:$login is:open`", type: ISSUE, first: 1) { issueCount }"
            $queryLines += "  ${aliasBase}_recent: search(query: `"repo:$Repo is:pr author:$login created:>$rateSinceQualifier`", type: ISSUE, first: 1) { issueCount }"
        }
        $queryLines += "}"

        $data = $null
        try {
            $result = Invoke-Gh api graphql -f query=($queryLines -join "`n") 2>$null | ConvertFrom-Json
            $data = $result.data
        } catch {}

        foreach ($idx in 0..($batch.Count - 1)) {
            $login = $batch[$idx]
            $aliasBase = "a$($offset + $idx)"
            $authorDates = if ($recentDatesByLogin.ContainsKey($login)) { @($recentDatesByLogin[$login] | Sort-Object) } else { @() }
            $last = if ($authorDates.Count -gt 0) { $authorDates[-1] } else { $null }

            if ($data) {
                $totalCount = Get-PropertyIssueCount -Data $data -Name "${aliasBase}_total"
                $openCount = Get-PropertyIssueCount -Data $data -Name "${aliasBase}_open"
                $recentCount = Get-PropertyIssueCount -Data $data -Name "${aliasBase}_recent"

                if ($null -ne $totalCount -and $null -ne $openCount -and $null -ne $recentCount) {
                    $credited = [math]::Max(0, $totalCount - $openCount)
                    $rate = if ($RateWindowDays -gt 0) { [math]::Round($recentCount / $RateWindowDays, 1) } else { 0 }
                    $idle = if ($last) { [math]::Round(($Now - $last).TotalDays, 1) } else { 999 }
                    $stats[$login] = @{
                        credited = $credited
                        open = $openCount
                        total = $totalCount
                        recentCount = $recentCount
                        rate = $rate
                        idle = $idle
                        lastCreatedAt = if ($last) { $last.ToString("o") } else { "" }
                        span = 0
                    }
                    continue
                }
            }

            $legacyStat = Get-LegacyLeaderboardStat -Repo $Repo -Login $login -RateSinceDate $RateSinceDate -Now $Now -RateWindowDays $RateWindowDays
            if ($legacyStat) {
                $stats[$login] = $legacyStat
            }
        }
    }

    return $stats
}

function New-ClassificationCache {
    return @{
        version = $ClassificationCacheVersion
        entries = @{}
        leaderboards = @{}
    }
}

function Import-ClassificationCache([string]$Path, [switch]$ForceRebuild, [switch]$ForceRebuildClassifications) {
    if ($ForceRebuild -or -not (Test-Path -LiteralPath $Path)) {
        return New-ClassificationCache
    }

    try {
        $raw = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        return New-ClassificationCache
    }

    if (-not $raw) {
        return New-ClassificationCache
    }

    $entries = @{}
    if (-not $ForceRebuildClassifications -and $raw.version -eq $ClassificationCacheVersion -and $raw.entries) {
        foreach ($prop in $raw.entries.PSObject.Properties) {
            $entries[$prop.Name] = @{
                classification = $prop.Value.classification
                release = $prop.Value.release
                viaLabel = $prop.Value.viaLabel
                viaUrl = $prop.Value.viaUrl
                evidenceKind = $prop.Value.evidenceKind
                cachedAt = $prop.Value.cachedAt
            }
        }
    }

    $leaderboards = @{}
    if ($raw.leaderboards) {
        foreach ($repoProp in $raw.leaderboards.PSObject.Properties) {
            $stats = @{}
            if ($repoProp.Value.stats) {
                foreach ($statProp in $repoProp.Value.stats.PSObject.Properties) {
                    $stats[$statProp.Name] = @{
                        total = $statProp.Value.total
                        open = $statProp.Value.open
                        recentCount = $statProp.Value.recentCount
                        lastCreatedAt = $statProp.Value.lastCreatedAt
                    }
                }
            }

            $logins = @()
            if ($repoProp.Value.logins) {
                $logins = @($repoProp.Value.logins | ForEach-Object { [string]$_ })
            }

            $shippedCounts = @{}
            if ($repoProp.Value.shippedCounts) {
                foreach ($shippedProp in $repoProp.Value.shippedCounts.PSObject.Properties) {
                    $shippedCounts[$shippedProp.Name] = [int]$shippedProp.Value
                }
            }

            $leaderboardKey = if ($repoProp.Name -match '\|') { $repoProp.Name } else { "$($repoProp.Name)|all" }
            $leaderboards[$leaderboardKey] = @{
                cachedAt = $repoProp.Value.cachedAt
                logins = $logins
                stats = $stats
                shippedCounts = $shippedCounts
            }
        }
    }

    return @{
        version = $ClassificationCacheVersion
        entries = $entries
        leaderboards = $leaderboards
    }
}

function Export-ClassificationCache([string]$Path) {
    $cacheForJson = [ordered]@{
        version = $script:ClassificationCache.version
        entries = [ordered]@{}
        leaderboards = [ordered]@{}
    }

    foreach ($key in ($script:ClassificationCache.entries.Keys | Sort-Object)) {
        $cacheForJson.entries[$key] = $script:ClassificationCache.entries[$key]
    }

    foreach ($repo in ($script:ClassificationCache.leaderboards.Keys | Sort-Object)) {
        $entry = $script:ClassificationCache.leaderboards[$repo]
        $stats = [ordered]@{}
        foreach ($login in ($entry.stats.Keys | Sort-Object)) {
            $stats[$login] = $entry.stats[$login]
        }

        $shippedCounts = [ordered]@{}
        if ($entry.shippedCounts) {
            foreach ($login in ($entry.shippedCounts.Keys | Sort-Object)) {
                $shippedCounts[$login] = [int]$entry.shippedCounts[$login]
            }
        }

        $cacheForJson.leaderboards[$repo] = [ordered]@{
            cachedAt = $entry.cachedAt
            logins = @($entry.logins)
            stats = $stats
            shippedCounts = $shippedCounts
        }
    }

    $cacheForJson | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $Path -Encoding utf8
}

function Get-ClassificationCacheKey([string]$Repo, [int]$Number) {
    return "$Repo#$Number"
}

function Get-ExistingClosedClassificationEntry([string]$Repo, [int]$Number) {
    $cacheKey = Get-ClassificationCacheKey -Repo $Repo -Number $Number
    if (-not $script:ClassificationCache.entries.ContainsKey($cacheKey)) {
        return $null
    }
    return $script:ClassificationCache.entries[$cacheKey]
}

function Get-CachedClosedClassification(
    [string]$Repo,
    [int]$Number,
    [datetime]$Now,
    [int]$TtlHours
) {
    $cacheKey = Get-ClassificationCacheKey -Repo $Repo -Number $Number
    if (-not $script:ClassificationCache.entries.ContainsKey($cacheKey)) {
        return $null
    }

    $entry = $script:ClassificationCache.entries[$cacheKey]
    if (-not $entry -or -not $entry.classification -or $entry.classification -eq "open") {
        return $null
    }

    if (-not $entry.cachedAt) {
        return $null
    }

    try {
        $cachedAt = [datetime]$entry.cachedAt
    } catch {
        return $null
    }

    if (($Now - $cachedAt).TotalHours -gt $TtlHours) {
        return $null
    }

    $script:ClassificationCacheHits++
    return $entry
}

function Set-CachedClosedClassification(
    [string]$Repo,
    [int]$Number,
    [string]$Classification,
    [string]$Release,
    [string]$ViaLabel,
    [string]$ViaUrl,
    [string]$EvidenceKind
) {
    $cacheKey = Get-ClassificationCacheKey -Repo $Repo -Number $Number

    if ($Classification -and $Classification -ne "open") {
        $script:ClassificationCache.entries[$cacheKey] = @{
            classification = $Classification
            release = $Release
            viaLabel = $ViaLabel
            viaUrl = $ViaUrl
            evidenceKind = $EvidenceKind
            cachedAt = (Get-Date).ToString("o")
        }
    } else {
        $script:ClassificationCache.entries.Remove($cacheKey) | Out-Null
    }
}

function Get-ClosedClassificationCacheTtlHours(
    [object]$PullRequest,
    [string]$Classification,
    [string]$EvidenceKind,
    [datetime]$Now
) {
    $closedAt = $null
    if ($PullRequest.closedAt) {
        try { $closedAt = [datetime]$PullRequest.closedAt } catch {}
    }

    $ageDays = if ($closedAt) { ($Now - $closedAt).TotalDays } else { 999 }

    switch ($Classification) {
        "shipped" {
            switch ($EvidenceKind) {
                "direct-merge" {
                    if ($ageDays -lt 30) { return 24 * 30 }
                    if ($ageDays -lt 120) { return 24 * 90 }
                    return 24 * 180
                }
                "timeline" {
                    if ($ageDays -lt 14) { return 24 * 14 }
                    if ($ageDays -lt 60) { return 24 * 30 }
                    return 24 * 90
                }
                default {
                    if ($ageDays -lt 14) { return 24 * 7 }
                    if ($ageDays -lt 60) { return 24 * 30 }
                    return 24 * 90
                }
            }
        }
        "accepted-indirect" {
            if ($ageDays -lt 14) { return 24 * 7 }
            if ($ageDays -lt 60) { return 24 * 30 }
            return 24 * 90
        }
        "lost" {
            if ($ageDays -lt 30) { return 24 * 30 }
            if ($ageDays -lt 120) { return 24 * 90 }
            return 24 * 180
        }
        "withdrawn" {
            if ($ageDays -lt 30) { return 24 * 30 }
            if ($ageDays -lt 120) { return 24 * 90 }
            return 24 * 180
        }
        default {
            return $ClosedClassificationCacheTtlHours
        }
    }
}

function Get-CachedLeaderboardStats(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [datetime]$Now,
    [double]$RateWindowDays,
    [int]$TtlHours,
    [switch]$ForceRefresh
) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    if ($ForceRefresh -or -not $script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        return $null
    }

    $entry = $script:ClassificationCache.leaderboards[$cacheKey]
    if (-not $entry -or -not $entry.cachedAt) {
        return $null
    }

    try {
        $cachedAt = [datetime]$entry.cachedAt
    } catch {
        return $null
    }

    if (($Now - $cachedAt).TotalHours -gt $TtlHours) {
        return $null
    }

    $stats = @{}
    foreach ($login in $entry.stats.Keys) {
        $raw = $entry.stats[$login]
        $totalCount = [int]$raw.total
        $openCount = [int]$raw.open
        $recentCount = [int]$raw.recentCount
        $last = $null
        if ($raw.lastCreatedAt) {
            try { $last = [datetime]$raw.lastCreatedAt } catch {}
        }

        $stats[$login] = @{
            credited = [math]::Max(0, $totalCount - $openCount)
            open = $openCount
            total = $totalCount
            recentCount = $recentCount
            rate = if ($RateWindowDays -gt 0) { [math]::Round($recentCount / $RateWindowDays, 1) } else { 0 }
            idle = if ($last) { [math]::Round(($Now - $last).TotalDays, 1) } else { 999 }
            lastCreatedAt = if ($raw.lastCreatedAt) { $raw.lastCreatedAt } else { "" }
            span = 0
        }
    }

    $shippedCounts = @{}
    if ($entry.shippedCounts) {
        foreach ($login in $entry.shippedCounts.Keys) {
            $shippedCounts[$login] = [int]$entry.shippedCounts[$login]
        }
    }

    $script:LeaderboardCacheHits++
    return @{
        logins = @($entry.logins)
        stats = $stats
        shippedCounts = $shippedCounts
    }
}

function Get-LeaderboardRefreshLogins(
    [hashtable]$Stats,
    [int]$Top = $LeaderboardMax
) {
    $refreshLogins = @($Stats.GetEnumerator() |
        Sort-Object { $_.Value.credited } -Descending |
        Select-Object -First $Top |
        ForEach-Object { $_.Key })
    if ($Stats.ContainsKey($Author) -and $refreshLogins -notcontains $Author) {
        $refreshLogins = @($refreshLogins + $Author)
    }
    return @($refreshLogins)
}

function Get-CommunityContributorLogins(
    [string]$Repo,
    [object[]]$RecentRepoPRs,
    [hashtable]$Exclusions
) {
    $uniqueLogins = @($RecentRepoPRs |
        ForEach-Object { $_.author.login } |
        Where-Object { $_ -and -not (Test-IsLeaderboardExcludedLogin -Login $_ -Exclusions $Exclusions) } |
        Select-Object -Unique)
    if ($uniqueLogins -notcontains $Author -and -not (Test-IsLeaderboardExcludedLogin -Login $Author -Exclusions $Exclusions)) {
        $uniqueLogins = @($Author) + $uniqueLogins
    }
    return @($uniqueLogins)
}

function Merge-CachedLeaderboardStats(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [string[]]$NewLogins,
    [hashtable]$NewStats
) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    if (-not $script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        return
    }

    $entry = $script:ClassificationCache.leaderboards[$cacheKey]
    foreach ($login in $NewStats.Keys) {
        $entry.stats[$login] = @{
            total = [int]$NewStats[$login].total
            open = [int]$NewStats[$login].open
            recentCount = [int]$NewStats[$login].recentCount
            lastCreatedAt = if ($NewStats[$login].lastCreatedAt) { [string]$NewStats[$login].lastCreatedAt } else { "" }
        }
    }

    $entry.logins = @($entry.logins + $NewLogins | Where-Object { $_ } | Select-Object -Unique)
}

function Set-CachedLeaderboardStats(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [string[]]$Logins,
    [hashtable]$Stats
) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    $storedStats = @{}
    foreach ($login in $Stats.Keys) {
        $storedStats[$login] = @{
            total = [int]$Stats[$login].total
            open = [int]$Stats[$login].open
            recentCount = [int]$Stats[$login].recentCount
            lastCreatedAt = if ($Stats[$login].lastCreatedAt) { [string]$Stats[$login].lastCreatedAt } else { "" }
        }
    }

    $existingShipped = @{}
    if ($script:ClassificationCache.leaderboards.ContainsKey($cacheKey) -and $script:ClassificationCache.leaderboards[$cacheKey].shippedCounts) {
        $existingShipped = $script:ClassificationCache.leaderboards[$cacheKey].shippedCounts
    }

    $script:ClassificationCache.leaderboards[$cacheKey] = @{
        cachedAt = (Get-Date).ToString("o")
        logins = @($Logins | Where-Object { $_ } | Select-Object -Unique)
        stats = $storedStats
        shippedCounts = $existingShipped
    }
}

function Set-CachedLeaderboardShippedCounts(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [hashtable]$ShippedCounts
) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    if (-not $script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        return
    }

    $stored = @{}
    foreach ($login in $ShippedCounts.Keys) {
        $stored[$login] = [int]$ShippedCounts[$login]
    }

    $script:ClassificationCache.leaderboards[$cacheKey].shippedCounts = $stored
}

function Get-ClosedPullRequestClassification([object]$PullRequest) {
    $cacheEntry = Get-ExistingClosedClassificationEntry -Repo $PullRequest.repo -Number $PullRequest.number
    $classificationCacheTtlHours = if ($cacheEntry) {
        Get-ClosedClassificationCacheTtlHours -PullRequest $PullRequest -Classification $cacheEntry.classification -EvidenceKind $cacheEntry.evidenceKind -Now (Get-Date)
    } else {
        $ClosedClassificationCacheTtlHours
    }
    $cachedClassification = Get-CachedClosedClassification -Repo $PullRequest.repo -Number $PullRequest.number -Now (Get-Date) -TtlHours $classificationCacheTtlHours
    if ($cachedClassification) {
        return @{
            Classification = $cachedClassification.classification
            Release = $cachedClassification.release
            ViaLabel = $cachedClassification.viaLabel
            ViaUrl = $cachedClassification.viaUrl
            EvidenceKind = $cachedClassification.evidenceKind
            FromCache = $true
            LogLabel = "$($cachedClassification.classification) (cache)"
        }
    }

    $raw = Get-PullRequestEvidence -Repo $PullRequest.repo -Number $PullRequest.number
    $comments = Get-NonBotCommentText -Evidence $raw
    $timelineNodes = @($raw.timelineItems.nodes)
    $closedEvent = @($timelineNodes | Where-Object { $_.__typename -eq "ClosedEvent" }) | Select-Object -First 1
    $mergedReleaseCloser = $null
    if ($closedEvent -and $closedEvent.closer.__typename -eq "PullRequest" -and $closedEvent.closer.merged -and (Test-IsReleaseTitle $closedEvent.closer.title)) {
        $mergedReleaseCloser = $closedEvent.closer
    }
    $mergedReleaseCrossRef = Select-BestCrossReference -Candidates @($timelineNodes |
            Where-Object {
                $_.__typename -eq "CrossReferencedEvent" -and
                $_.source.__typename -eq "PullRequest" -and
                $_.source.merged -and
                (Test-IsReleaseTitle $_.source.title)
            }) -ClosedAt $closedEvent.createdAt
    $releaseRefCommit = ($timelineNodes |
            Where-Object {
                $_.__typename -eq "ReferencedEvent" -and
                (Test-IsReleaseTitle $_.commit.messageHeadline)
            } |
            ForEach-Object { $_.commit } |
            Select-Object -First 1)

    $release = ""
    foreach ($candidate in @(
        $comments,
        $(if ($mergedReleaseCloser) { $mergedReleaseCloser.title }),
        $(if ($mergedReleaseCrossRef) { $mergedReleaseCrossRef.title }),
        $(if ($releaseRefCommit) { $releaseRefCommit.messageHeadline })
    )) {
        $release = Get-ReleaseTag $candidate
        if ($release) { break }
    }

    $isDirectMerged = $PullRequest.state -eq "MERGED" -or [bool]$PullRequest.mergedAt
    $isTimelineShipped = [bool]($mergedReleaseCloser -or $mergedReleaseCrossRef -or $releaseRefCommit)
    $isShipped = $false
    foreach ($p in $shippedPatterns) { if ($comments -match [regex]::Escape($p)) { $isShipped = $true; break } }
    $isAccepted = $false
    foreach ($p in $acceptedPatterns) { if ($comments -match [regex]::Escape($p)) { $isAccepted = $true; break } }
    $isDuplicate = $false
    foreach ($p in $duplicatePatterns) { if ($comments -match [regex]::Escape($p)) { $isDuplicate = $true; break } }
    $isSuperseded = Test-IsSupersededEvidence -PullRequest $PullRequest -Evidence $raw
    $hasSupersededReference = Test-HasSupersededReference -Evidence $raw
    $isLost = $false
    foreach ($p in $lostPatterns) { if ($comments -match [regex]::Escape($p)) { $isLost = $true; break } }
    $isAuthorWithdrawn = Test-IsAuthorWithdrawnEvidence -PullRequest $PullRequest -Evidence $raw
    $acceptedSibling = Get-TimelineCreditedMergedPullRequest -Repo $PullRequest.repo -OriginalPr $PullRequest -Evidence $raw
    if (-not $acceptedSibling -and ($isDuplicate -or $isSuperseded -or $isLost -or $comments)) {
        $acceptedSibling = Get-ReferencedMergedPullRequest -Repo $PullRequest.repo -OriginalPr $PullRequest -Text $comments
    }

    $viaLabel = ""
    $viaUrl = ""
    $classification = "withdrawn"
    $evidenceKind = "withdrawn"
    $logLabel = "withdrawn"

    if ($isDirectMerged -or $isTimelineShipped) {
        $classification = "shipped"
        if ($isDirectMerged) {
            $viaLabel = "direct"
            $viaUrl = "https://github.com/$($PullRequest.repo)/pull/$($PullRequest.number)"
            $logLabel = "shipped (merged directly)"
        } elseif ($mergedReleaseCloser) {
            $viaLabel = "#$($mergedReleaseCloser.number)"
            $viaUrl = $mergedReleaseCloser.url
            $logLabel = "shipped (released via #$($mergedReleaseCloser.number))"
        } elseif ($mergedReleaseCrossRef) {
            $viaLabel = "#$($mergedReleaseCrossRef.number)"
            $viaUrl = $mergedReleaseCrossRef.url
            $logLabel = "shipped (referenced by merged #$($mergedReleaseCrossRef.number))"
        } elseif ($releaseRefCommit) {
            $viaLabel = $releaseRefCommit.oid.Substring(0, 7)
            $viaUrl = $releaseRefCommit.url
            $logLabel = "shipped (referenced by release commit)"
        } else {
            $logLabel = "shipped"
        }
        $evidenceKind = if ($isDirectMerged) { "direct-merge" } elseif ($isTimelineShipped) { "timeline" } else { "comment" }
    } elseif ($isAuthorWithdrawn) {
        $classification = "withdrawn"
        $evidenceKind = "author-withdrawn"
        $logLabel = "withdrawn (author withdrew)"
    } elseif ($isSuperseded) {
        $classification = "superseded"
        $evidenceKind = "superseded"
        $logLabel = "superseded"
    } elseif ($isAccepted -or $acceptedSibling) {
        $classification = "accepted-indirect"
        $evidenceKind = "accepted-indirect"
        if ($acceptedSibling) {
            $viaLabel = "#$($acceptedSibling.number)"
            $viaUrl = $acceptedSibling.url
            $logLabel = "accepted indirectly via #$($acceptedSibling.number)"
        } else {
            $logLabel = "accepted (indirect)"
        }
    } elseif ($isDuplicate -or $isLost) {
        $classification = "lost"
        $evidenceKind = "lost"
        $logLabel = "lost (competing PR won)"
    } elseif ($hasSupersededReference) {
        $classification = "lost"
        $evidenceKind = "lost"
        $logLabel = "lost (superseded without maintainer credit)"
    } elseif ($isShipped) {
        $classification = "shipped"
        $evidenceKind = "comment"
        $logLabel = "shipped"
    } elseif (-not $comments -or $comments.Trim().Length -eq 0) {
        $classification = "withdrawn"
        $evidenceKind = "withdrawn"
        $logLabel = "withdrawn (no maintainer interaction)"
    }

    Set-CachedClosedClassification -Repo $PullRequest.repo -Number $PullRequest.number -Classification $classification -Release $release -ViaLabel $viaLabel -ViaUrl $viaUrl -EvidenceKind $evidenceKind
    return @{
        Classification = $classification
        Release = $release
        ViaLabel = $viaLabel
        ViaUrl = $viaUrl
        EvidenceKind = $evidenceKind
        FromCache = $false
        LogLabel = $logLabel
    }
}

function Get-ContributorShippedCount(
    [string]$Repo,
    [string]$Login,
    [int]$PrecomputedShipped = -1
) {
    if ($PrecomputedShipped -ge 0) {
        return $PrecomputedShipped
    }

    $repoShort = ($Repo -split '/')[-1]
    $raw = Invoke-Gh pr list --repo $Repo --author $Login --state all --limit 500 --json 'number,state,title,createdAt,closedAt,mergedAt,author' 2>$null
    if (-not $raw) { return 0 }
    $prs = @($raw | ConvertFrom-Json)
    $closed = @($prs | Where-Object { $_.state -eq "CLOSED" -or $_.state -eq "MERGED" })
    if ($closed.Count -eq 0) { return 0 }

    Write-ProgressHost "      $Login — classifying $($closed.Count) closed PRs on $repoShort..." -ForegroundColor DarkGray

    $shippedCount = 0
    $index = 0
    foreach ($pr in $closed) {
        $index++
        Write-ProgressHost "        [$index/$($closed.Count)] #$($pr.number)..." -NoNewline
        $pr | Add-Member -NotePropertyName repo -NotePropertyValue $Repo -Force
        $pr | Add-Member -NotePropertyName repoShort -NotePropertyValue $repoShort -Force
        $result = Get-ClosedPullRequestClassification -PullRequest $pr
        $color = if ($result.FromCache) { "DarkGray" } elseif ($result.Classification -in @("shipped", "accepted-indirect")) { "Green" } else { "DarkYellow" }
        Write-ProgressHost " $($result.LogLabel)" -ForegroundColor $color
        if ($result.Classification -in @("shipped", "accepted-indirect")) {
            $shippedCount++
        }
    }
    return $shippedCount
}

$script:ClassificationCache = Import-ClassificationCache -Path $CacheFile -ForceRebuild:$RebuildCache -ForceRebuildClassifications:$RebuildClassifications

Write-Host "Fetching PRs from $($Repos.Count) repos..." -ForegroundColor DarkGray

$allPRs = @()
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repo..." -ForegroundColor DarkGray
    $prs = Invoke-Gh pr list --repo $repo --author $Author --state all --limit 500 --json 'number,state,title,createdAt,closedAt,mergedAt,headRefName,author' 2>$null | ConvertFrom-Json
    foreach ($pr in $prs) {
        $pr | Add-Member -NotePropertyName repo -NotePropertyValue $repo -Force
        $pr | Add-Member -NotePropertyName repoShort -NotePropertyValue $repoShort -Force
        $pr | Add-Member -NotePropertyName classification -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName release -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName viaLabel -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName viaUrl -NotePropertyValue "" -Force
        $allPRs += $pr
    }
}

$closed = @($allPRs | Where-Object { $_.state -eq "CLOSED" -or $_.state -eq "MERGED" })
$open = @($allPRs | Where-Object { $_.state -eq "OPEN" })
foreach ($pr in $open) {
    $pr.classification = "open"
}

Write-ProgressHost "Classifying $($closed.Count) closed PRs..." -ForegroundColor DarkGray

$shipped = @(); $acceptedIndirect = @(); $duplicates = @(); $lost = @(); $superseded = @(); $withdrawn = @(); $rejected = @()

foreach ($pr in $closed) {
    Write-ProgressHost "  #$($pr.number) ($($pr.repoShort))..." -ForegroundColor DarkGray -NoNewline
    $result = Get-ClosedPullRequestClassification -PullRequest $pr
    $pr.classification = $result.Classification
    $pr.release = $result.Release
    $pr.viaLabel = $result.ViaLabel
    $pr.viaUrl = $result.ViaUrl

    switch ($result.Classification) {
        "shipped" {
            $shipped += $pr
            $color = "Green"
        }
        "accepted-indirect" {
            $acceptedIndirect += $pr
            $color = "Cyan"
        }
        "lost" {
            $lost += $pr
            $color = "Red"
        }
        "superseded" {
            $superseded += $pr
            $color = "DarkYellow"
        }
        default {
            $withdrawn += $pr
            $color = "DarkGray"
        }
    }
    Write-ProgressHost " $($result.LogLabel)" -ForegroundColor $color
}

$startDateValue = Get-OptionalDateValue -Date $StartDate
if ($null -eq $startDateValue -and $Span -ne "All") {
    $startDateValue = $DefaultReportStartDate
}

if ($null -ne $startDateValue) {
    $allPRs = @($allPRs | Where-Object {
        $statusKey = if ($_.classification) { $_.classification } else { "open" }
        $effectiveDate = Get-PullRequestEffectiveIsoDate -PullRequest $_ -StatusKey $statusKey
        if (-not $effectiveDate) { return $false }
        try {
            return [datetime]$effectiveDate -ge $startDateValue
        } catch {
            return $false
        }
    })
}

$closed = @($allPRs | Where-Object { $_.classification -and $_.classification -ne "open" })
$open = @($allPRs | Where-Object { $_.classification -eq "open" })
$shipped = @($allPRs | Where-Object { $_.classification -eq "shipped" })
$acceptedIndirect = @($allPRs | Where-Object { $_.classification -eq "accepted-indirect" })
$lost = @($allPRs | Where-Object { $_.classification -eq "lost" })
$superseded = @($allPRs | Where-Object { $_.classification -eq "superseded" })
$withdrawn = @($allPRs | Where-Object { $_.classification -eq "withdrawn" })
$rejected = @($allPRs | Where-Object { $_.classification -eq "rejected" })

$totalAccepted = $shipped.Count + $acceptedIndirect.Count
$totalNotShipped = $lost.Count + $superseded.Count + $withdrawn.Count
$totalClosed = $totalAccepted + $lost.Count + $superseded.Count + $withdrawn.Count
$acceptanceRate = if ($totalClosed -gt 0) { [math]::Round(($totalAccepted / $totalClosed) * 100) } else { "N/A" }

# Build per-repo leaderboards
Write-Host "`nBuilding leaderboards..." -ForegroundColor DarkGray
$now = Get-Date
$leaderboardRateSinceDate = $now.AddDays(-$LeaderboardRateWindowDays)
$reportStartLabel = if ($null -ne $startDateValue) { Get-ReportStartLabel -Date $startDateValue } else { "" }

$leaderboardHtml = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repoShort contributors..." -ForegroundColor DarkGray

    $exclusions = Get-RepoLeaderboardExclusions -Repo $repo
    $cachedShippedCounts = @{}

    $repoPRs = @(Get-RecentRepoPullRequests -Repo $repo -Limit 500)
    if ($repoPRs.Count -eq 0) { continue }
    $scannedLogins = Get-CommunityContributorLogins -Repo $repo -RecentRepoPRs $repoPRs -Exclusions $exclusions

    $cachedLeaderboard = Get-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -Now $now -RateWindowDays $LeaderboardRateWindowDays -TtlHours $LeaderboardCacheTtlHours -ForceRefresh:$RefreshLeaderboardCache
    if ($cachedLeaderboard) {
        $stats = $cachedLeaderboard.stats
        if ($cachedLeaderboard.shippedCounts) {
            $cachedShippedCounts = $cachedLeaderboard.shippedCounts
        }

        $cachedCount = if ($cachedLeaderboard.logins.Count -gt 0) { $cachedLeaderboard.logins.Count } else { $stats.Count }
        $newLogins = @($scannedLogins | Where-Object { -not $stats.ContainsKey($_) })
        if ($newLogins.Count -gt 0) {
            $newStats = Get-LeaderboardStats -Repo $repo -Logins $newLogins -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
            foreach ($login in $newStats.Keys) {
                $stats[$login] = $newStats[$login]
            }
            Merge-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -NewLogins $newLogins -NewStats $newStats
        }

        $refreshLogins = @(Get-LeaderboardRefreshLogins -Stats $stats | Where-Object { $newLogins -notcontains $_ })
        if ($refreshLogins.Count -gt 0) {
            $refreshStats = Get-LeaderboardStats -Repo $repo -Logins $refreshLogins -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
            foreach ($login in $refreshStats.Keys) {
                $stats[$login] = $refreshStats[$login]
            }
            Merge-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -NewLogins @() -NewStats $refreshStats
        }

        $logParts = @("$cachedCount contributors from cache")
        if ($newLogins.Count -gt 0) { $logParts += "$($newLogins.Count) new" }
        if ($refreshLogins.Count -gt 0) { $logParts += "top $($refreshLogins.Count) refreshed" }
        Write-Host "    $($logParts -join ', ')..." -ForegroundColor DarkGray
    } else {
        Write-Host "    $($scannedLogins.Count) community contributors found, fetching batched counts..." -ForegroundColor DarkGray

        $stats = Get-LeaderboardStats -Repo $repo -Logins $scannedLogins -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
        Set-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -Logins $scannedLogins -Stats $stats
    }

    $communityStats = @{}
    foreach ($login in $stats.Keys) {
        if (Test-IsLeaderboardExcludedLogin -Login $login -Exclusions $exclusions) { continue }
        $entry = $stats[$login]
        $communityStats[$login] = @{
            credited = [int]$entry.credited
            open = [int]$entry.open
            total = [int]$entry.total
            recentCount = [int]$entry.recentCount
            rate = [double]$entry.rate
            idle = [double]$entry.idle
            lastCreatedAt = if ($entry.lastCreatedAt) { [string]$entry.lastCreatedAt } else { "" }
            span = if ($entry.span) { [double]$entry.span } else { 0 }
            estimated = $true
            shippedClassified = $false
        }
    }

    if ($communityStats.Count -eq 0) { continue }

    $myRepoCredited = @($shipped | Where-Object { $_.repo -eq $repo }).Count + @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $classifyLogins = @($communityStats.GetEnumerator() |
        Sort-Object { $_.Value.credited } -Descending |
        Select-Object -First $LeaderboardClassifyTop |
        ForEach-Object { $_.Key })
    if ($communityStats.ContainsKey($Author) -and $classifyLogins -notcontains $Author) {
        $classifyLogins = @($classifyLogins + $Author)
    }

    $useCachedShipped = (-not $RefreshLeaderboardCache) -and $cachedShippedCounts.Count -gt 0
    $shippedCountsToSave = @{}
    $classifiedFromCache = 0
    $classifiedLive = 0

    Write-Host "    Resolving shipped counts for $($classifyLogins.Count) of $($communityStats.Count) community contributors..." -ForegroundColor DarkGray
    foreach ($login in $classifyLogins) {
        if ($login -eq $Author) {
            $shippedCount = $myRepoCredited
        } elseif ($useCachedShipped -and $cachedShippedCounts.ContainsKey($login)) {
            $shippedCount = [int]$cachedShippedCounts[$login]
            $classifiedFromCache++
        } else {
            $shippedCount = Get-ContributorShippedCount -Repo $repo -Login $login
            $classifiedLive++
            Write-Host "      $login shipped=$shippedCount (classified)" -ForegroundColor DarkGray
        }

        $shippedCountsToSave[$login] = $shippedCount
        $communityStats[$login].credited = $shippedCount
        $communityStats[$login].estimated = $false
        $communityStats[$login].shippedClassified = $true
    }

    if ($classifiedFromCache -gt 0 -or $classifiedLive -gt 0) {
        Write-Host "    $classifiedFromCache shipped totals from cache, $classifiedLive newly classified" -ForegroundColor DarkGray
    }
    Set-CachedLeaderboardShippedCounts -Repo $repo -StartDate $StartDate -ShippedCounts $shippedCountsToSave

    $allSorted = @($communityStats.GetEnumerator() | Sort-Object { $_.Value.credited } -Descending)
    $myRank = 1
    foreach ($entry in $allSorted) { if ($entry.Key -eq $Author) { break }; $myRank++ }

    $sorted = @($allSorted | Select-Object -First $LeaderboardMax)
    $totalCommunity = $allSorted.Count
    $totalContributors = $sorted.Count
    $expandLabel = if ($totalCommunity -gt $LeaderboardMax) {
        "Show top $LeaderboardMax"
    } else {
        "Show all $totalCommunity contributors"
    }

    $leaderboardRows = ""
    $visibleStart = 1
    $visibleEnd = [math]::Min($DefaultLeaderboardVisible, $totalContributors)
    $collapseMode = "top"
    if ($repoShort -eq "hermes-agent" -and $totalContributors -gt $DefaultLeaderboardVisible) {
        $visibleStart = [math]::Max(1, [math]::Min($myRank - 4, $totalContributors - $DefaultLeaderboardVisible + 1))
        $visibleEnd = [math]::Min($totalContributors, $visibleStart + $DefaultLeaderboardVisible - 1)
        $collapseMode = "context"
    }
    $rank = 1
    foreach ($entry in $sorted) {
        $s = $entry.Value
        $name = $entry.Key
        $isMe = $name -eq $Author
        $statusLabel = if ($s.idle -lt 1) { "Active" } elseif ($s.idle -lt 3) { "Recent" } elseif ($s.idle -lt 7) { "Slowing" } elseif ($s.idle -lt 14) { "Quiet" } else { "Gone" }
        $statusClass = if ($s.idle -lt 3) { "green" } elseif ($s.idle -lt 7) { "yellow" } else { "dim" }
        $rowClasses = @()
        if ($isMe) { $rowClasses += "is-self" }
        if ($collapseMode -eq "context" -and ($rank -lt $visibleStart -or $rank -gt $visibleEnd)) { $rowClasses += "context-hidden" }
        $rowClass = if ($rowClasses.Count -gt 0) { " class=`"$($rowClasses -join ' ')`"" } else { "" }
        $nameDisplay = if ($isMe) { "$name" } else { $name }
        $leaderboardRows += "  <tr$rowClass data-rank=`"$rank`"><td>#$rank</td><td><a href=`"https://github.com/$name`">$nameDisplay</a></td><td>$($s.credited)</td><td>$($s.open)</td><td>$($s.rate)/d</td><td><span class=`"$statusClass`">$statusLabel</span></td></tr>`n"
        if ($collapseMode -eq "top" -and $rank -eq $DefaultLeaderboardVisible -and $totalContributors -gt $DefaultLeaderboardVisible) {
            $leaderboardRows += "  <tr class=`"expand-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`"><td colspan=`"6`">$expandLabel <span class=`"caret`">&#9660;</span></td></tr>`n"
        } elseif ($collapseMode -eq "context" -and $rank -eq $visibleEnd -and $totalContributors -gt $DefaultLeaderboardVisible) {
            $leaderboardRows += "  <tr class=`"expand-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`"><td colspan=`"6`">$expandLabel <span class=`"caret`">&#9660;</span></td></tr>`n"
        }
        $rank++
    }

    # Build projections for contributors ahead of me
    $projectionsHtml = ""
    $myCredited = if ($communityStats.ContainsKey($Author)) { $communityStats[$Author].credited } else { 0 }
    $myRate = if ($communityStats.ContainsKey($Author)) { $communityStats[$Author].rate } else { 0 }
    $ahead = @($allSorted | Where-Object { $_.Value.credited -gt $myCredited })

    if ($ahead.Count -gt 0 -and $myRate -gt 0) {
        $projRows = ""
        foreach ($entry in $ahead) {
            $s = $entry.Value
            $gap = $s.credited - $myCredited
            $netRate = $myRate - $s.rate
            if ($netRate -le 0) {
                $projRows += "  <tr><td>$($entry.Key)</td><td>$($s.credited) (+$gap)</td><td>$($s.rate)/d</td><td class=`"red`">not at current rates</td></tr>`n"
            } else {
                $days = [math]::Round($gap / $netRate, 1)
                $when = $now.AddDays($days)
                $projRows += "  <tr><td>$($entry.Key)</td><td>$($s.credited) (+$gap)</td><td>$($s.rate)/d</td><td>${days}d ($($when.ToString("MMM d")))</td></tr>`n"
            }
        }
        $projectionsHtml = @"
<details class="projections">
<summary>Projections ($Author @ $myRate/day Rate (7d), rank #$myRank)</summary>
<table>
  <tr><th>Contributor</th><th>Credited</th><th>Rate (7d)</th><th>Catch-up</th></tr>
$projRows</table>
</details>
"@
    } elseif ($myRank -eq 1) {
        $projectionsHtml = @"
<details class="projections">
<summary>Projections ($Author @ $myRate/day Rate (7d), rank #1)</summary>
<p class="note">No contributors ahead at current credited totals.</p>
</details>
"@
    }

    $collapsedClass = if ($totalContributors -gt $DefaultLeaderboardVisible) { " collapsed" } else { "" }
    $overlayHtml = if ($totalContributors -gt $DefaultLeaderboardVisible) { "<div class=`"overlay-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`">Collapse <span class=`"caret`">&#9650;</span></div>`n" } else { "" }
    $isAgent = $repoShort -eq "hermes-agent"
    $leaderboardHtml += @"
<h2>$repoShort Community Leaderboard</h2>
<div class="collapsible-table leaderboard$collapsedClass" id="lb-$repoShort" data-collapse-mode="$collapseMode">
<table>
  <thead><tr><th>Rank</th><th>Contributor</th><th>Credited</th><th>Open</th><th>Rate (7d)</th><th>Status</th></tr></thead>
  <tbody>
$leaderboardRows  </tbody>
</table>
$overlayHtml
</div>
$projectionsHtml

"@
    Export-ClassificationCache -Path $CacheFile
}

Export-ClassificationCache -Path $CacheFile

if (Test-Path -LiteralPath $ReadmePath) {
    Write-Host "`nReading representative PRs from $ReadmePath..." -ForegroundColor DarkGray
    $readmeText = Get-Content -Raw -Path $ReadmePath
} else {
    Write-Host "`nFetching representative PRs from $ReadmeRepo README..." -ForegroundColor DarkGray
    $readmeB64 = Invoke-Gh api "repos/$ReadmeRepo/contents/README.md" --jq '.content' 2>$null
    $readmeText = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($readmeB64 -replace "\s","")))
}

function Format-RepresentativeReleaseCell(
    [string]$Release,
    [string]$ReleaseUrl,
    [string]$Classification
) {
    if ($Release -and $ReleaseUrl) {
        return "<a href=`"$ReleaseUrl`">$Release</a>"
    }
    if ($Release) {
        return $Release
    }
    if ($Classification -eq "accepted-indirect") {
        return "indirect"
    }
    return ""
}

function Format-RepresentativeViaCell([string]$ViaLabel, [string]$ViaUrl) {
    if ($ViaLabel -and $ViaUrl) {
        return "<a href=`"$ViaUrl`">$ViaLabel</a>"
    }
    if ($ViaLabel) {
        return $ViaLabel
    }
    return ""
}

$representativeHtml = ""
$inBlock = $false; $representativeItems = @()
foreach ($line in ($readmeText -split "`n")) {
    if ($line -match "^Representative merged PRs:") { $inBlock = $true; continue }
    if ($inBlock) {
        if ($line -match "^##" -or ($line -notmatch "^-" -and $representativeItems.Count -gt 0 -and $line -notmatch "^\s")) { break }
        if ($line -match "^-\s*\[#(\d+)\]\(([^)]+)\)") {
            $prNum = $Matches[1]; $prUrl = $Matches[2]
            $desc = $line -replace "^-\s*\[#\d+\]\([^)]+\)\s*", ""
            $desc = $desc -replace '^\W+\s*', ''
            $release = ""
            $releaseUrl = ""
            if ($desc -match '\s*\(\[([^\]]+)\]\(([^)]+)\)\)\s*$') {
                $release = $Matches[1]
                $releaseUrl = $Matches[2]
                $desc = $desc -replace '\s*\(\[[^\]]+\]\([^)]+\)\)\s*$', ''
            }
            $desc = $desc -replace '\[([^\]]+)\]\(([^)]+)\)', '<a href="$2">$1</a>'
            $desc = $desc.TrimEnd()
            $repoPath = ""
            $repoPathMatch = [regex]::Match($prUrl, 'github\.com/([^/]+/[^/]+)/pull/')
            if ($repoPathMatch.Success) {
                $repoPath = $repoPathMatch.Groups[1].Value
            }
            $viaLabel = ""
            $viaUrl = ""
            $classification = ""
            $matchedPr = $allPRs | Where-Object { "$($_.repo)" -eq $repoPath -and "$($_.number)" -eq $prNum } | Select-Object -First 1
            if ($matchedPr) {
                $classification = Get-ScalarValue $matchedPr.classification
                if (-not $release -and $matchedPr.release) {
                    $release = Get-ScalarValue $matchedPr.release
                    if ($release) {
                        $releaseUrl = "https://github.com/$repoPath/releases/tag/$release"
                    }
                } elseif (-not $release -and $classification -eq "accepted-indirect") {
                    $release = "indirect"
                }
                $viaLabel = Get-ScalarValue $matchedPr.viaLabel
                $viaUrl = Get-ScalarValue $matchedPr.viaUrl
            }
            $representativeItems += [pscustomobject][ordered]@{
                num = $prNum
                url = $prUrl
                repo = $repoPath
                repoLabel = if ($repoPath) { Get-RepoLabel -Repo $repoPath } else { "" }
                desc = $desc
                release = $release
                releaseUrl = $releaseUrl
                viaLabel = $viaLabel
                viaUrl = $viaUrl
                classification = $classification
            }
        }
    }
}

if ($representativeItems.Count -gt 0) {
    $representativeHtml = @"
    <h2>Representative PRs</h2>
<table class="rep-prs-table shipped-prs">
  <tr><th>PR</th><th>Repo</th><th>Description</th><th>Release</th><th>Via</th></tr>

"@
    foreach ($m in $representativeItems) {
        $releaseCell = Format-RepresentativeReleaseCell -Release $m.release -ReleaseUrl $m.releaseUrl -Classification $m.classification
        $viaCell = Format-RepresentativeViaCell -ViaLabel $m.viaLabel -ViaUrl $m.viaUrl
        $representativeHtml += "  <tr class=`"rep-main-row`"><td><a href=`"$($m.url)`">#$($m.num)</a></td><td>$($m.repoLabel)</td><td class=`"rep-desc-cell`">$($m.desc)</td><td>$releaseCell</td><td>$viaCell</td></tr>`n"
        $representativeHtml += "  <tr class=`"rep-desc-row`"><td class=`"rep-desc-gap`"></td><td colspan=`"4`"><div class=`"rep-desc-text`">$($m.desc)</div></td></tr>`n"
    }
    $representativeHtml += "</table>"
} else {
    $representativeHtml = '<p class="empty-state">Representative PRs unavailable.</p>'
}

Write-Host "Generating HTML..." -ForegroundColor DarkGray

$allPRItems = @()
foreach ($pr in $allPRs) {
    $classificationKey = if ($pr.classification) { $pr.classification } else { "open" }
    $statusKey = if ($classificationKey -eq "accepted-indirect") { "shipped" } else { $classificationKey }
    $statusLabel = ""
    $statusClass = ""
    $releaseLabel = ""

    switch ($classificationKey) {
        "shipped" {
            $statusLabel = "Shipped"
            $statusClass = "tag-shipped"
            $releaseLabel = if ($pr.release) { $pr.release } else { "" }
        }
        "accepted-indirect" {
            $statusLabel = "Shipped"
            $statusClass = "tag-shipped"
            $releaseLabel = "indirect"
        }
        "lost" {
            $statusLabel = "Lost"
            $statusClass = "tag-lost"
        }
        "superseded" {
            $statusLabel = "Superseded"
            $statusClass = "tag-superseded"
        }
        "withdrawn" {
            $statusLabel = "Withdrawn"
            $statusClass = "tag-withdrawn"
        }
        default {
            $statusLabel = "Open"
            $statusClass = "tag-open"
        }
    }

    $effectiveDate = Get-PullRequestEffectiveIsoDate -PullRequest $pr -StatusKey $statusKey
    $sortDate = if ($effectiveDate) { [datetime]$effectiveDate } else { [datetime]::MinValue }
    $displayDate = Format-EasternDate $effectiveDate

    $allPRItems += [pscustomobject][ordered]@{
        number = $pr.number
        repo = $pr.repo
        repoLabel = Get-RepoLabel -Repo $pr.repo
        title = $pr.title
        statusKey = $statusKey
        statusLabel = $statusLabel
        statusClass = $statusClass
        dateLabel = $displayDate
        releaseLabel = Get-ScalarValue $releaseLabel
        viaLabel = Get-ScalarValue $pr.viaLabel
        viaUrl = Get-ScalarValue $pr.viaUrl
        sortDate = $sortDate
    }
}
$allPRItems = @($allPRItems | Sort-Object sortDate -Descending)

function Test-PrStatusMatch {
    param(
        [string]$StatusKey,
        [string]$ItemStatusKey
    )
    if ($StatusKey -eq "not-shipped") {
        return $ItemStatusKey -in @("lost", "superseded", "withdrawn")
    }
    return $ItemStatusKey -eq $StatusKey
}

function Test-PrRepoMatch {
    param(
        [string]$RepoKey,
        [string]$RepoLabel
    )
    return $RepoKey -eq "all" -or $RepoLabel -eq $RepoKey
}

function Get-PrFilterCount {
    param(
        $Items,
        [string]$StatusKey,
        [string]$RepoKey
    )
    @($Items | Where-Object {
        (Test-PrStatusMatch -StatusKey $StatusKey -ItemStatusKey $_.statusKey) -and
        (Test-PrRepoMatch -RepoKey $RepoKey -RepoLabel $_.repoLabel)
    }).Count
}

$defaultPrStatusKey = "shipped"
$defaultPrRepoKey = "all"

$prStatusFilters = @(
    [pscustomobject][ordered]@{ key = "open"; label = "Open"; count = $open.Count },
    [pscustomobject][ordered]@{ key = "shipped"; label = "Shipped"; count = $totalAccepted },
    [pscustomobject][ordered]@{ key = "not-shipped"; label = "Not Shipped"; count = $totalNotShipped }
)

$prFilterPills = ""
foreach ($filter in $prStatusFilters) {
    $activeClass = if ($filter.key -eq $defaultPrStatusKey) { " active" } else { "" }
    $pillCount = Get-PrFilterCount -Items $allPRItems -StatusKey $filter.key -RepoKey $defaultPrRepoKey
    $prFilterPills += "    <div class=`"sort-pill$activeClass`" data-status=`"$($filter.key)`">$($filter.label) ($pillCount)</div>`n"
}

$prRepoFilters = @(
    [pscustomobject][ordered]@{
        key = "all"
        label = "All"
        count = (Get-PrFilterCount -Items $allPRItems -StatusKey $defaultPrStatusKey -RepoKey "all")
    }
)
foreach ($repo in $Repos) {
    $repoLabel = Get-RepoLabel -Repo $repo
    $repoCount = Get-PrFilterCount -Items $allPRItems -StatusKey $defaultPrStatusKey -RepoKey $repoLabel
    $prRepoFilters += [pscustomobject][ordered]@{
        key = $repoLabel
        label = $repoLabel
        count = $repoCount
    }
}

$prRepoPills = ""
foreach ($filter in $prRepoFilters) {
    $activeClass = if ($filter.key -eq $defaultPrRepoKey) { " active" } else { "" }
    $prRepoPills += "    <div class=`"sort-pill$activeClass`" data-repo=`"$($filter.key)`">$($filter.label)</div>`n"
}

$prDataJson = @(
    $allPRItems | ForEach-Object {
        [pscustomobject][ordered]@{
            number = $_.number
            url = "https://github.com/$($_.repo)/pull/$($_.number)"
            repoLabel = $_.repoLabel
            title = $_.title
            statusKey = $_.statusKey
            statusLabel = $_.statusLabel
            statusClass = $_.statusClass
            dateLabel = $_.dateLabel
            releaseLabel = $_.releaseLabel
            viaLabel = $_.viaLabel
            viaUrl = $_.viaUrl
        }
    }
) | ConvertTo-Json -Depth 4 -Compress
$prDataJson = $prDataJson -replace '</script', '<\/script'
$prFiltersJson = @($prStatusFilters) | ConvertTo-Json -Compress
$prFiltersJson = $prFiltersJson -replace '</script', '<\/script'
$prRepoFiltersJson = @($prRepoFilters) | ConvertTo-Json -Compress
$prRepoFiltersJson = $prRepoFiltersJson -replace '</script', '<\/script'

$repoSections = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    $repoPRs = @($allPRs | Where-Object { $_.repo -eq $repo })
    $repoOpen = @($repoPRs | Where-Object { $_.state -eq "OPEN" }).Count
    $repoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count + @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $repoLost = @($lost | Where-Object { $_.repo -eq $repo }).Count
    $repoSuperseded = @($superseded | Where-Object { $_.repo -eq $repo }).Count
    $repoWithdrawn = @($withdrawn | Where-Object { $_.repo -eq $repo }).Count
    $repoRejected = @($rejected | Where-Object { $_.repo -eq $repo }).Count

    $repoSections += @"
<h2>$repoShort ($($repoPRs.Count) PRs)</h2>
<table>
  <tr><th>Status</th><th>Count</th><th>Details</th></tr>
  <tr><td><span class="tag tag-shipped">Shipped</span></td><td>$repoShipped</td><td>Verified via merged release PR, maintainer release evidence, or indirect accepted sibling</td></tr>
  <tr><td><span class="tag tag-open">Open</span></td><td>$repoOpen</td><td>Awaiting maintainer review</td></tr>
$(if ($repoWithdrawn -gt 0) { "  <tr><td><span class=`"tag tag-withdrawn`">Withdrawn</span></td><td>$repoWithdrawn</td><td>Closed without maintainer action</td></tr>`n" })$(if ($repoSuperseded -gt 0) { "  <tr><td><span class=`"tag tag-superseded`">Superseded</span></td><td>$repoSuperseded</td><td>Replaced by a newer maintainer-accepted PR</td></tr>`n" })$(if ($repoLost -gt 0) { "  <tr><td><span class=`"tag tag-lost`">Lost</span></td><td>$repoLost</td><td>Competing PR won</td></tr>`n" })</table>

"@
}

$dateStr = $now.ToString("MMMM d, yyyy")

# Calculate time span from earliest to latest PR
$allDates = @()
foreach ($pr in $allPRs) {
    $statusKey = if ($pr.classification) { $pr.classification } else { "open" }
    $effectiveDate = Get-PullRequestEffectiveIsoDate -PullRequest $pr -StatusKey $statusKey
    if ($effectiveDate) { try { $allDates += [datetime]$effectiveDate } catch {} }
}
$allDates = @($allDates | Sort-Object)
if ($allDates.Count -ge 2) {
    $spanDays = [math]::Floor(($allDates[-1] - $allDates[0]).TotalDays)
    $timeSpan = if ($spanDays -eq 1) { "1 day" } else { "$spanDays days" }
    $displayEndDate = $allDates[0].AddDays($spanDays)
    $timeRange = "$($allDates[0].ToString('MMMM d'))-$($displayEndDate.ToString('MMMM d, yyyy'))"
} else {
    $timeSpan = "N/A"
    $timeRange = ""
}

$barShipped = if ($allPRs.Count -gt 0) { [math]::Round(($totalAccepted / $allPRs.Count) * 100, 1) } else { 0 }
$barLost = if ($allPRs.Count -gt 0) { [math]::Round(($lost.Count / $allPRs.Count) * 100, 1) } else { 0 }
$barSuperseded = if ($allPRs.Count -gt 0) { [math]::Round(($superseded.Count / $allPRs.Count) * 100, 1) } else { 0 }
$barWithdrawn = if ($allPRs.Count -gt 0) { [math]::Round(($withdrawn.Count / $allPRs.Count) * 100, 1) } else { 0 }
$barOpen = if ($allPRs.Count -gt 0) { [math]::Round(($open.Count / $allPRs.Count) * 100, 1) } else { 0 }

$html = @"
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>GitHub Contributions</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="darkreader-lock" />
    <meta name="description" content="150+ PRs across OSS AI projects: provider infrastructure, agent UX, reliability, streaming, and release-linked production work." />
    <meta property="og:title" content="GitHub Contributions">
    <meta property="og:description" content="150+ PRs across OSS AI projects: provider infrastructure, agent UX, reliability, streaming, and release-linked production work.">
    <meta property="og:image" content="https://basedin.nyc/pr-stats/thumb.jpg">
    <meta property="og:url" content="https://basedin.nyc/pr-stats">
    <meta property="og:type" content="website">
    <meta name="darkreader-lock" />
    <meta name="color-scheme" content="light dark" />
    <link rel="stylesheet" href="../style.css?v=20260611f">
  </head>
<body class="pr">

<div class="top-row">
  <h1><a class="back-link" href="../"><svg viewBox="0 0 16 16" width="1em" height="1em"><path d="M10 2L4 8l6 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>GitHub Contributions</h1>
  <nav class="nav-links">
    <a href="../projects/">Projects</a>
    <span class="nav-sep">/</span>
    <span class="current">Stats</span>
    <span class="nav-sep">/</span>
    <span class="nav-repo"><a href="https://github.com/rodboev/pr-sweep">Repo</a><span class="private">(private)</span></span>
  </nav>
</div>

<div class="grid grid-summary">
  <div class="stat-card"><div class="number">$($allPRs.Count)</div><div class="label">Total PRs</div></div>
  <div class="stat-card"><div class="number green">$totalAccepted</div><div class="label">Shipped</div></div>
  <div class="stat-card"><div class="number blue">$($open.Count)</div><div class="label">Open</div></div>
  <div class="stat-card"><div class="number">$totalNotShipped</div><div class="label">Not Shipped</div></div>
</div>
<div class="grid">
  <div class="stat-card"><div class="number green">${acceptanceRate}%</div><div class="label">Acceptance rate ($($withdrawn.Count) withdrawn, $($superseded.Count) superseded, $($lost.Count) lost)</div></div>
  <div class="stat-card"><div class="number yellow">$timeSpan</div><div class="label">Time span ($timeRange)</div></div>
</div>

<h2>Breakdown</h2>

<div class="bar-container">
  <div class="bar-segment bar-shipped" data-width="${barShipped}"$(if ($barShipped -gt 4) { " title=`"$totalAccepted`"" })>$(if ($barShipped -gt 4) { $totalAccepted })</div>
  <div class="bar-segment bar-withdrawn" data-width="${barWithdrawn}">$(if ($barWithdrawn -gt 4) { $withdrawn.Count })</div>
  <div class="bar-segment bar-superseded" data-width="${barSuperseded}">$(if ($barSuperseded -gt 4) { $superseded.Count })</div>
  <div class="bar-segment bar-lost" data-width="${barLost}">$(if ($barLost -gt 4) { $lost.Count })</div>
  <div class="bar-segment bar-open" data-width="${barOpen}" title="$($open.Count)">$($open.Count)</div>
</div>
<div class="legend">
  <div class="legend-item"><div class="legend-dot legend-dot-shipped"></div> Shipped ($totalAccepted)</div>
  <div class="legend-item"><div class="legend-dot legend-dot-withdrawn"></div> Withdrawn ($($withdrawn.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-superseded"></div> Superseded ($($superseded.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-lost"></div> Lost ($($lost.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-open"></div> Open ($($open.Count))</div>
</div>

$repoSections

$leaderboardHtml

$representativeHtml

<div class="landscape-row">
  <div class="pr-filter-group pr-filter-group-left">
    <h2>PRs</h2>
    <div class="sort-pills" id="pr-repo-pills">
$prRepoPills    </div>
  </div>
  <div class="pr-filter-group pr-filter-group-right">
    <div class="sort-pills" id="pr-filter-pills">
$prFilterPills    </div>
  </div>
</div>
<table class="targets-table pr-list-table" id="pr-list-table">
  <colgroup>
    <col class="pr-col-pr">
    <col class="pr-col-repo">
    <col class="pr-col-status">
    <col class="pr-col-date">
    <col class="pr-col-release">
    <col class="pr-col-via">
  </colgroup>
  <thead><tr><th>PR</th><th>Repo</th><th>Status</th><th>Date</th><th>Release</th><th>Via</th></tr></thead>
  <tbody id="pr-list-body"></tbody>
</table>

<h2>Methodology</h2>
<div class="section methodology-section">
  <p><strong>Shipped</strong> means maintainer-accepted work: a direct merge, timeline evidence such as a release PR or cherry-pick reference, or an indirect landing credited back to the original PR. GitHub's <code>mergedAt</code> flag alone is not sufficient because some repos land contributor work outside the merge button.</p>
  <p><strong>Withdrawn</strong>, <strong>superseded</strong>, and <strong>lost</strong> separate author pullbacks, maintainer replacements, and competing outcomes. The PR table is sorted newest-first using close time for closed PRs and creation time for open PRs.$(if ($null -ne $startDateValue) { " This report includes PRs with an effective date on or after $reportStartLabel." })</p>
  <p><strong>Community leaderboards</strong> rank third-party contributors only. Repo owners, maintainers, integration bots, and other automated accounts are excluded. Each board shows the top $LeaderboardMax contributors. For the top $LeaderboardClassifyTop ranked contributors, <strong>Credited</strong> uses the same lifetime shipped evidence rules as this page across all closed PRs returned by GitHub (up to 500 per author). Lower rows use a faster closed-PR proxy. <strong>Rate (7d)</strong> is PRs opened in the rolling last $LeaderboardRateWindowDays days divided by $LeaderboardRateWindowDays (a recent per-day opening rate, not merge velocity).</p>
</div>

<p class="footer">Generated $dateStr from GitHub API. Source: <a href="https://github.com/$ReadmeRepo">$ReadmeRepo</a></p>

<script>
var PR_FILTERS = $prFiltersJson;
var PR_REPO_FILTERS = $prRepoFiltersJson;
var PR_DATA = $prDataJson;
var CURRENT_PR_FILTER = {
  statusKey: 'shipped',
  repoKey: 'all'
};

function setBarWidths() {
  document.querySelectorAll('.bar-segment[data-width]').forEach(function(segment) {
    segment.style.width = segment.getAttribute('data-width') + '%';
  });
}
function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function prDateCell(dateLabel) {
  if (!dateLabel) return '';
  var shortDate = dateLabel.replace(/\s+\d{1,2}:\d{2}\s*[AP]M$/i, '');
  return '<span class="pr-date-full">' + escapeHtml(dateLabel) + '</span>' +
    '<span class="pr-date-short">' + escapeHtml(shortDate) + '</span>';
}
function syncLandscapeStickyOffset() {
  var row = document.querySelector('.landscape-row');
  if (!row) return;
  document.body.style.setProperty('--landscape-row-offset', row.getBoundingClientRect().height + 'px');
}
function prMatchesStatus(item, statusKey) {
  if (statusKey === 'not-shipped') {
    return item.statusKey === 'lost' || item.statusKey === 'superseded' || item.statusKey === 'withdrawn';
  }
  return item.statusKey === statusKey;
}
function prMatchesRepo(item, repoKey) {
  return repoKey === 'all' || item.repoLabel === repoKey;
}
function countPrItems(statusKey, repoKey) {
  return PR_DATA.filter(function(item) {
    return prMatchesStatus(item, statusKey) && prMatchesRepo(item, repoKey);
  }).length;
}
function updatePrFilterPills() {
  PR_FILTERS.forEach(function(filter) {
    var pill = document.querySelector('#pr-filter-pills .sort-pill[data-status="' + filter.key + '"]');
    if (!pill) return;
    var count = countPrItems(filter.key, CURRENT_PR_FILTER.repoKey);
    pill.textContent = filter.label + ' (' + count + ')';
  });
}
function renderPrTable(statusKey, repoKey) {
  var tbody = document.getElementById('pr-list-body');
  if (!tbody) return;
  var filtered = PR_DATA.filter(function(item) {
    return prMatchesStatus(item, statusKey) && prMatchesRepo(item, repoKey);
  });
  var html = '';
  filtered.forEach(function(item) {
    var via = '';
    if (item.viaLabel) {
      via = item.viaUrl ? '<a href="' + item.viaUrl + '">' + item.viaLabel + '</a>' : item.viaLabel;
    }
    html += '<tr class="pr-main-row">' +
      '<td><a href="' + item.url + '">#' + item.number + '</a></td>' +
      '<td>' + item.repoLabel + '</td>' +
      '<td><span class="tag ' + item.statusClass + '">' + item.statusLabel + '</span></td>' +
      '<td>' + prDateCell(item.dateLabel) + '</td>' +
      '<td>' + (item.releaseLabel || '') + '</td>' +
      '<td>' + via + '</td>' +
    '</tr>' +
    '<tr class="pr-title-row">' +
      '<td class="pr-title-gap"></td>' +
      '<td colspan="5"><div class="pr-title-text" title="' + escapeHtml(item.title || '') + '">' + escapeHtml(item.title || '') + '</div></td>' +
    '</tr>';
  });
  if (!html) {
    html = '<tr><td colspan="6" class="empty-state">No PRs in this status.</td></tr>';
  }
  tbody.innerHTML = html;
}
function updateCollapsedOverlays() {
  document.querySelectorAll('.collapsible-table:not(.collapsed)').forEach(function(block) {
    var tbody = block.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.from(tbody.querySelectorAll('tr:not(.expand-row)'));
    if (!rows.length) return;
    var overlay = block.querySelector('.overlay-row');
    if (!overlay) return;
    var lastRow = rows[rows.length - 1];
    if (lastRow.getBoundingClientRect().bottom > window.innerHeight) {
      overlay.classList.add('visible');
    } else {
      overlay.classList.remove('visible');
    }
  });
}
function toggleCollapsedTable(id) {
  var el = document.getElementById(id);
  var wasCollapsed = el.classList.contains('collapsed');
  var collapseMode = el.getAttribute('data-collapse-mode');
  var anchor = null;
  var anchorTop = 0;
  if (collapseMode === 'context') {
    anchor = el.querySelector('tr.is-self') || el.querySelector('tr[data-rank]');
    if (anchor) anchorTop = anchor.getBoundingClientRect().top;
  }
  el.classList.toggle('collapsed');
  if (collapseMode === 'context' && anchor) {
    var newTop = anchor.getBoundingClientRect().top;
    window.scrollBy({ top: newTop - anchorTop, behavior: 'auto' });
  } else if (!wasCollapsed) {
    el.scrollIntoView({ block: 'start', behavior: 'auto' });
  }
  updateCollapsedOverlays();
}
function toggleLeaderboard(id) {
  toggleCollapsedTable(id);
}
document.getElementById('pr-filter-pills').addEventListener('click', function(e) {
  var pill = e.target.closest('.sort-pill');
  if (!pill) return;
  CURRENT_PR_FILTER.statusKey = pill.getAttribute('data-status');
  document.querySelectorAll('#pr-filter-pills .sort-pill').forEach(function(p) {
    p.classList.remove('active');
  });
  pill.classList.add('active');
  updatePrFilterPills();
  renderPrTable(CURRENT_PR_FILTER.statusKey, CURRENT_PR_FILTER.repoKey);
});
document.getElementById('pr-repo-pills').addEventListener('click', function(e) {
  var pill = e.target.closest('.sort-pill');
  if (!pill) return;
  CURRENT_PR_FILTER.repoKey = pill.getAttribute('data-repo');
  document.querySelectorAll('#pr-repo-pills .sort-pill').forEach(function(p) {
    p.classList.remove('active');
  });
  pill.classList.add('active');
  updatePrFilterPills();
  renderPrTable(CURRENT_PR_FILTER.statusKey, CURRENT_PR_FILTER.repoKey);
});
setBarWidths();
syncLandscapeStickyOffset();
if (typeof ResizeObserver !== 'undefined') {
  var landscapeRow = document.querySelector('.landscape-row');
  if (landscapeRow) {
    new ResizeObserver(syncLandscapeStickyOffset).observe(landscapeRow);
  }
}
window.addEventListener('resize', syncLandscapeStickyOffset);
updatePrFilterPills();
renderPrTable(CURRENT_PR_FILTER.statusKey, CURRENT_PR_FILTER.repoKey);
updateCollapsedOverlays();
document.addEventListener('scroll', updateCollapsedOverlays, { passive: true });
</script>
<script src="../assets/script.js?v=20260609u"></script>

</body>
</html>
"@

$html | Out-File -FilePath $OutFile -Encoding utf8
Write-Host "`nWritten to $OutFile" -ForegroundColor Green
Write-Host "  Total: $($allPRs.Count) | Shipped: $totalAccepted | Open: $($open.Count) | Lost: $($lost.Count) | Rate: ${acceptanceRate}%"
Write-Host "  Closed classification cache hits: $script:ClassificationCacheHits | Leaderboard cache hits: $script:LeaderboardCacheHits | Cache file: $CacheFile" -ForegroundColor DarkGray
$generateElapsed = (Get-Date) - $script:GenerateStartedAt
Write-Host "  Elapsed: $([int]$generateElapsed.TotalSeconds)s ($([math]::Round($generateElapsed.TotalMinutes, 1)) min)" -ForegroundColor DarkGray
