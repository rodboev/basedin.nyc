param(
    [string]$Author = "rodboev",
    [string[]]$Repos = @("nesquena/hermes-webui", "NousResearch/hermes-agent"),
    [string]$ReadmeRepo = "rodboev/pr-sweep",
    [string]$OutFile = "$PSScriptRoot\index.html",
    [string]$CacheFile = "$PSScriptRoot\.pr-classification-cache.json",
    [int]$ClosedClassificationCacheTtlHours = 24 * 30,
    [int]$LeaderboardCacheTtlHours = 24,
    [int]$LeaderboardTop = 10,
    [switch]$RebuildCache,
    [switch]$RefreshLeaderboardCache,
    [switch]$OpenOutput
)

$shippedPatterns = @("Shipped", "shipped", "cherry-picked", "merged-via", "Salvaged into", "salvaged into")
$acceptedPatterns = @()
$duplicatePatterns = @("Duplicate", "duplicate")
$lostPatterns = @("Superseded by", "superseded by", "consolidated", "Consolidating")
$withdrawnPattern = '(?i)\bwithdraw(?:ing|n)?\b'
$DefaultLeaderboardVisible = 10
$ClassificationCacheVersion = 2

$EasternTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
$script:PullRequestStateCache = @{}
$script:PullRequestEvidenceCache = @{}
$script:ClassificationCache = @{
    version = $ClassificationCacheVersion
    entries = @{}
    leaderboards = @{}
}
$script:ClassificationCacheHits = 0
$script:LeaderboardCacheHits = 0

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
        $commentResult = @(gh api "repos/$Repo/issues/$Number/comments?per_page=100" 2>$null | ConvertFrom-Json)
    } catch {}

    $timelineResult = @()
    try {
        $timelineResult = @(gh api "repos/$Repo/issues/$Number/timeline?per_page=100" -H "Accept: application/vnd.github+json" 2>$null | ConvertFrom-Json)
    } catch {}

    $commentNodes = foreach ($comment in $commentResult) {
        [pscustomobject]@{
            body = $comment.body
            author = [pscustomobject]@{
                login = $comment.user.login
            }
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

function Get-PullRequestState([string]$Repo, [int]$Number) {
    $cacheKey = "$Repo#$Number"
    if ($script:PullRequestStateCache.ContainsKey($cacheKey)) {
        return $script:PullRequestStateCache[$cacheKey]
    }
    $result = gh pr view $Number --repo $Repo --json number,state,mergedAt,title,url,author,body 2>$null | ConvertFrom-Json
    $script:PullRequestStateCache[$cacheKey] = $result
    return $result
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

function Get-PullRequestReferenceText([string]$Repo, [int]$Number) {
    $details = Get-PullRequestState -Repo $Repo -Number $Number
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
        $pr = Get-PullRequestState -Repo $Repo -Number $num
        if ($pr -and ($pr.state -eq "MERGED" -or $pr.mergedAt) -and (Test-IsCreditedMergedSibling -Repo $Repo -OriginalPr $OriginalPr -MergedPr $pr)) {
            return $pr
        }
    }
    return $null
}

function Get-RecentRepoPullRequests([string]$Repo, [int]$Limit = 500) {
    $raw = gh pr list --repo $Repo --state all --limit $Limit --json author,createdAt,state 2>$null
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
    [datetime]$SinceDate,
    [datetime]$Now,
    [double]$DaysSinceStart
) {
    $aRaw = gh pr list --repo $Repo --author $Login --state all --limit 500 --json number,createdAt,state 2>$null
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

    $sinceDateCount = 0
    foreach ($pr in $prs) {
        if ($pr.createdAt) {
            try {
                if ([datetime]$pr.createdAt -gt $SinceDate) { $sinceDateCount++ }
            } catch {}
        }
    }

    $rate = if ($DaysSinceStart -gt 0) { [math]::Round($sinceDateCount / $DaysSinceStart, 1) } else { 0 }
    $last = if ($dates.Count -gt 0) { $dates[-1] } else { $null }
    $idle = if ($last) { [math]::Round(($Now - $last).TotalDays, 1) } else { 999 }
    $span = if ($dates.Count -ge 2) { ($dates[-1] - $dates[0]).TotalDays } else { 0 }

    return @{
        credited = $credited
        open = $openCount
        total = $prs.Count
        recentCount = $sinceDateCount
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
    [datetime]$SinceDate,
    [datetime]$Now,
    [double]$DaysSinceStart
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
    $sinceDateQualifier = $SinceDate.ToString("yyyy-MM-dd")

    for ($offset = 0; $offset -lt $authorList.Count; $offset += $batchSize) {
        $batch = @($authorList | Select-Object -Skip $offset -First $batchSize)
        $queryLines = @("query {")
        for ($idx = 0; $idx -lt $batch.Count; $idx++) {
            $login = $batch[$idx]
            $aliasBase = "a$($offset + $idx)"
            $queryLines += "  ${aliasBase}_total: search(query: `"repo:$Repo is:pr author:$login`", type: ISSUE, first: 1) { issueCount }"
            $queryLines += "  ${aliasBase}_open: search(query: `"repo:$Repo is:pr author:$login is:open`", type: ISSUE, first: 1) { issueCount }"
            $queryLines += "  ${aliasBase}_recent: search(query: `"repo:$Repo is:pr author:$login created:>$sinceDateQualifier`", type: ISSUE, first: 1) { issueCount }"
        }
        $queryLines += "}"

        $data = $null
        try {
            $result = gh api graphql -f query=($queryLines -join "`n") 2>$null | ConvertFrom-Json
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
                    $rate = if ($DaysSinceStart -gt 0) { [math]::Round($recentCount / $DaysSinceStart, 1) } else { 0 }
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

            $legacyStat = Get-LegacyLeaderboardStat -Repo $Repo -Login $login -SinceDate $SinceDate -Now $Now -DaysSinceStart $DaysSinceStart
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

function Import-ClassificationCache([string]$Path, [switch]$ForceRebuild) {
    if ($ForceRebuild -or -not (Test-Path -LiteralPath $Path)) {
        return New-ClassificationCache
    }

    try {
        $raw = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        return New-ClassificationCache
    }

    if (-not $raw -or $raw.version -ne $ClassificationCacheVersion) {
        return New-ClassificationCache
    }

    $entries = @{}
    if ($raw.entries) {
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

            $leaderboards[$repoProp.Name] = @{
                cachedAt = $repoProp.Value.cachedAt
                logins = $logins
                stats = $stats
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

        $cacheForJson.leaderboards[$repo] = [ordered]@{
            cachedAt = $entry.cachedAt
            logins = @($entry.logins)
            stats = $stats
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
    [datetime]$Now,
    [double]$DaysSinceStart,
    [int]$TtlHours,
    [switch]$ForceRefresh
) {
    if ($ForceRefresh -or -not $script:ClassificationCache.leaderboards.ContainsKey($Repo)) {
        return $null
    }

    $entry = $script:ClassificationCache.leaderboards[$Repo]
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
            rate = if ($DaysSinceStart -gt 0) { [math]::Round($recentCount / $DaysSinceStart, 1) } else { 0 }
            idle = if ($last) { [math]::Round(($Now - $last).TotalDays, 1) } else { 999 }
            lastCreatedAt = if ($raw.lastCreatedAt) { $raw.lastCreatedAt } else { "" }
            span = 0
        }
    }

    $script:LeaderboardCacheHits++
    return @{
        logins = @($entry.logins)
        stats = $stats
    }
}

function Set-CachedLeaderboardStats(
    [string]$Repo,
    [string[]]$Logins,
    [hashtable]$Stats
) {
    $storedStats = @{}
    foreach ($login in $Stats.Keys) {
        $storedStats[$login] = @{
            total = [int]$Stats[$login].total
            open = [int]$Stats[$login].open
            recentCount = [int]$Stats[$login].recentCount
            lastCreatedAt = if ($Stats[$login].lastCreatedAt) { [string]$Stats[$login].lastCreatedAt } else { "" }
        }
    }

    $script:ClassificationCache.leaderboards[$Repo] = @{
        cachedAt = (Get-Date).ToString("o")
        logins = @($Logins | Where-Object { $_ } | Select-Object -Unique)
        stats = $storedStats
    }
}

$script:ClassificationCache = Import-ClassificationCache -Path $CacheFile -ForceRebuild:$RebuildCache

Write-Host "Fetching PRs from $($Repos.Count) repos..." -ForegroundColor DarkGray

$allPRs = @()
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repo..." -ForegroundColor DarkGray
    $prs = gh pr list --repo $repo --author $Author --state all --limit 500 --json number,state,title,createdAt,closedAt,mergedAt,headRefName,author 2>$null | ConvertFrom-Json
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

Write-Host "Classifying $($closed.Count) closed PRs..." -ForegroundColor DarkGray

$shipped = @(); $acceptedIndirect = @(); $duplicates = @(); $lost = @(); $withdrawn = @(); $rejected = @()

foreach ($pr in $closed) {
    Write-Host "  #$($pr.number) ($($pr.repoShort))..." -ForegroundColor DarkGray -NoNewline
    $cacheEntry = Get-ExistingClosedClassificationEntry -Repo $pr.repo -Number $pr.number
    $classificationCacheTtlHours = if ($cacheEntry) {
        Get-ClosedClassificationCacheTtlHours -PullRequest $pr -Classification $cacheEntry.classification -EvidenceKind $cacheEntry.evidenceKind -Now (Get-Date)
    } else {
        $ClosedClassificationCacheTtlHours
    }
    $cachedClassification = Get-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Now (Get-Date) -TtlHours $classificationCacheTtlHours
    if ($cachedClassification) {
        $pr.classification = $cachedClassification.classification
        $pr.release = $cachedClassification.release
        $pr.viaLabel = $cachedClassification.viaLabel
        $pr.viaUrl = $cachedClassification.viaUrl
        switch ($pr.classification) {
            "shipped" {
                $shipped += $pr
                Write-Host " shipped (cache)" -ForegroundColor Green
            }
            "accepted-indirect" {
                $acceptedIndirect += $pr
                Write-Host " accepted indirectly (cache)" -ForegroundColor Cyan
            }
            "lost" {
                $lost += $pr
                Write-Host " lost (cache)" -ForegroundColor Red
            }
            "withdrawn" {
                $withdrawn += $pr
                Write-Host " withdrawn (cache)" -ForegroundColor DarkGray
            }
            default {
                Write-Host " $($pr.classification) (cache)" -ForegroundColor DarkGray
            }
        }
        continue
    }

    $raw = Get-PullRequestEvidence -Repo $pr.repo -Number $pr.number
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
    $mergedPullRequestCloser = $null
    if ($closedEvent -and $closedEvent.closer.__typename -eq "PullRequest" -and $closedEvent.closer.merged) {
        $mergedPullRequestCloser = $closedEvent.closer
    }
    $mergedPullRequestCrossRef = Select-BestCrossReference -Candidates @($timelineNodes |
            Where-Object {
                $_.__typename -eq "CrossReferencedEvent" -and
                $_.source.__typename -eq "PullRequest" -and
                $_.source.merged
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
    $pr.release = $release

    $isDirectMerged = $pr.state -eq "MERGED" -or [bool]$pr.mergedAt
    $isTimelineShipped = $false
    if ($mergedReleaseCloser -or $mergedReleaseCrossRef -or $mergedPullRequestCloser -or $mergedPullRequestCrossRef -or $releaseRefCommit) {
        $isTimelineShipped = $true
    }

    $isShipped = $false
    foreach ($p in $shippedPatterns) { if ($comments -match [regex]::Escape($p)) { $isShipped = $true; break } }

    $isAccepted = $false
    foreach ($p in $acceptedPatterns) { if ($comments -match [regex]::Escape($p)) { $isAccepted = $true; break } }

    $isDuplicate = $false
    foreach ($p in $duplicatePatterns) { if ($comments -match [regex]::Escape($p)) { $isDuplicate = $true; break } }

    $isLost = $false
    foreach ($p in $lostPatterns) { if ($comments -match [regex]::Escape($p)) { $isLost = $true; break } }

    $isAuthorWithdrawn = Test-IsAuthorWithdrawnEvidence -PullRequest $pr -Evidence $raw

    $acceptedSibling = $null
    if ($isDuplicate -or $isLost) {
        $acceptedSibling = Get-ReferencedMergedPullRequest -Repo $pr.repo -OriginalPr $pr -Text $comments
    }

    if ($isDirectMerged -or $isTimelineShipped -or $isShipped) {
        $pr.classification = "shipped"
        $shipped += $pr
        if ($isDirectMerged) {
            $pr.viaLabel = "direct"
            $pr.viaUrl = "https://github.com/$($pr.repo)/pull/$($pr.number)"
            Write-Host " shipped (merged directly)" -ForegroundColor Green
        } elseif ($isTimelineShipped) {
            $sourceLabel = if ($mergedReleaseCloser) {
                $pr.viaLabel = "#$($mergedReleaseCloser.number)"
                $pr.viaUrl = $mergedReleaseCloser.url
                "released via #$($mergedReleaseCloser.number)"
            } elseif ($mergedReleaseCrossRef) {
                $pr.viaLabel = "#$($mergedReleaseCrossRef.number)"
                $pr.viaUrl = $mergedReleaseCrossRef.url
                "referenced by merged #$($mergedReleaseCrossRef.number)"
            } elseif ($mergedPullRequestCloser) {
                $pr.viaLabel = "#$($mergedPullRequestCloser.number)"
                $pr.viaUrl = $mergedPullRequestCloser.url
                "closed by merged #$($mergedPullRequestCloser.number)"
            } elseif ($mergedPullRequestCrossRef) {
                $pr.viaLabel = "#$($mergedPullRequestCrossRef.number)"
                $pr.viaUrl = $mergedPullRequestCrossRef.url
                "referenced by merged #$($mergedPullRequestCrossRef.number)"
            } else {
                $pr.viaLabel = $releaseRefCommit.oid.Substring(0, 7)
                $pr.viaUrl = $releaseRefCommit.url
                "referenced by release commit"
            }
            Write-Host " shipped ($sourceLabel)" -ForegroundColor Green
        } else {
            Write-Host " shipped" -ForegroundColor Green
        }
        $evidenceKind = if ($isDirectMerged) { "direct-merge" } elseif ($isTimelineShipped) { "timeline" } else { "comment" }
        Set-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Classification $pr.classification -Release $pr.release -ViaLabel $pr.viaLabel -ViaUrl $pr.viaUrl -EvidenceKind $evidenceKind
    } elseif ($isAuthorWithdrawn) {
        $pr.classification = "withdrawn"
        $withdrawn += $pr
        Write-Host " withdrawn (author withdrew)" -ForegroundColor DarkGray
        Set-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Classification $pr.classification -Release $pr.release -ViaLabel $pr.viaLabel -ViaUrl $pr.viaUrl -EvidenceKind "author-withdrawn"
    } elseif ($isAccepted -or $acceptedSibling) {
        $pr.classification = "accepted-indirect"
        $acceptedIndirect += $pr
        if ($acceptedSibling) {
            $pr.viaLabel = "#$($acceptedSibling.number)"
            $pr.viaUrl = $acceptedSibling.url
            Write-Host " accepted indirectly via #$($acceptedSibling.number)" -ForegroundColor Cyan
        } else {
            Write-Host " accepted (indirect)" -ForegroundColor Cyan
        }
        Set-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Classification $pr.classification -Release $pr.release -ViaLabel $pr.viaLabel -ViaUrl $pr.viaUrl -EvidenceKind "accepted-indirect"
    } elseif ($isDuplicate -or $isLost) {
        $pr.classification = "lost"
        $lost += $pr
        Write-Host " lost (competing PR won)" -ForegroundColor Red
        Set-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Classification $pr.classification -Release $pr.release -ViaLabel $pr.viaLabel -ViaUrl $pr.viaUrl -EvidenceKind "lost"
    } elseif (-not $comments -or $comments.Trim().Length -eq 0) {
        $pr.classification = "withdrawn"
        $withdrawn += $pr
        Write-Host " withdrawn (no maintainer interaction)" -ForegroundColor DarkGray
        Set-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Classification $pr.classification -Release $pr.release -ViaLabel $pr.viaLabel -ViaUrl $pr.viaUrl -EvidenceKind "withdrawn"
    } else {
        $pr.classification = "withdrawn"
        $withdrawn += $pr
        Write-Host " withdrawn" -ForegroundColor DarkGray
        Set-CachedClosedClassification -Repo $pr.repo -Number $pr.number -Classification $pr.classification -Release $pr.release -ViaLabel $pr.viaLabel -ViaUrl $pr.viaUrl -EvidenceKind "withdrawn"
    }
}

$totalAccepted = $shipped.Count + $acceptedIndirect.Count
$totalLostWithdrawn = $lost.Count + $withdrawn.Count
$totalClosed = $totalAccepted + $lost.Count + $withdrawn.Count
$acceptanceRate = if ($totalClosed -gt 0) { [math]::Round(($totalAccepted / $totalClosed) * 100) } else { "N/A" }

# Build per-repo leaderboards
Write-Host "`nBuilding leaderboards..." -ForegroundColor DarkGray
$now = Get-Date
$jun1 = [datetime]"2026-06-01"
$daysSinceJun1 = ($now - $jun1).TotalDays

$leaderboardHtml = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repoShort contributors..." -ForegroundColor DarkGray

    $cachedLeaderboard = Get-CachedLeaderboardStats -Repo $repo -Now $now -DaysSinceStart $daysSinceJun1 -TtlHours $LeaderboardCacheTtlHours -ForceRefresh:$RefreshLeaderboardCache
    if ($cachedLeaderboard) {
        $stats = $cachedLeaderboard.stats
        $cachedCount = if ($cachedLeaderboard.logins.Count -gt 0) { $cachedLeaderboard.logins.Count } else { $stats.Count }
        Write-Host "    $cachedCount contributors loaded from cache..." -ForegroundColor DarkGray
    } else {
        # Discover contributors from the most recent 500 PRs, then fetch batched counts.
        $repoPRs = @(Get-RecentRepoPullRequests -Repo $repo -Limit 500)
        if ($repoPRs.Count -eq 0) { continue }
        $uniqueLogins = @($repoPRs | ForEach-Object { $_.author.login } | Where-Object { $_ -and $_ -ne "nesquena-hermes" } | Select-Object -Unique)
        if ($uniqueLogins -notcontains $Author) { $uniqueLogins = @($Author) + $uniqueLogins }

        Write-Host "    $($uniqueLogins.Count) contributors found, fetching batched counts..." -ForegroundColor DarkGray

        $stats = Get-LeaderboardStats -Repo $repo -Logins $uniqueLogins -RecentRepoPRs $repoPRs -SinceDate $jun1 -Now $now -DaysSinceStart $daysSinceJun1
        Set-CachedLeaderboardStats -Repo $repo -Logins $uniqueLogins -Stats $stats
    }

    # Use my classified count for this repo instead of raw closed
    $myRepoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count
    $myRepoIndirect = @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $myRepoCredited = $myRepoShipped + $myRepoIndirect
    if ($stats.ContainsKey($Author)) {
        $stats[$Author].credited = $myRepoCredited
    }

    $sorted = $stats.GetEnumerator() | Sort-Object { $_.Value.credited } -Descending
    $myRank = 1
    foreach ($entry in $sorted) { if ($entry.Key -eq $Author) { break }; $myRank++ }

    $leaderboardRows = ""
    $totalContributors = $sorted.Count
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
            $leaderboardRows += "  <tr class=`"expand-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`"><td colspan=`"6`">Show all $totalContributors contributors <span class=`"caret`">&#9660;</span></td></tr>`n"
        } elseif ($collapseMode -eq "context" -and $rank -eq $visibleEnd -and $totalContributors -gt $DefaultLeaderboardVisible) {
            $leaderboardRows += "  <tr class=`"expand-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`"><td colspan=`"6`">Show all $totalContributors contributors <span class=`"caret`">&#9660;</span></td></tr>`n"
        }
        $rank++
    }

    # Build projections for contributors ahead of me
    $projectionsHtml = ""
    $myCredited = if ($stats.ContainsKey($Author)) { $stats[$Author].credited } else { 0 }
    $myRate = if ($stats.ContainsKey($Author)) { $stats[$Author].rate } else { 0 }
    $ahead = @($sorted | Where-Object { $_.Value.credited -gt $myCredited })

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
<summary>Projections (rodboev @ $myRate/day, rank #$myRank)</summary>
<table>
  <tr><th>Contributor</th><th>Credited</th><th>Rate</th><th>Catch-up</th></tr>
$projRows</table>
</details>
"@
    } elseif ($myRank -eq 1) {
        $projectionsHtml = "<p class=`"note projections-note`">Rank #1 at $myRate/day</p>"
    }

    $collapsedClass = if ($totalContributors -gt $DefaultLeaderboardVisible) { " collapsed" } else { "" }
    $overlayHtml = if ($totalContributors -gt $DefaultLeaderboardVisible) { "<div class=`"overlay-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`">Collapse <span class=`"caret`">&#9650;</span></div>`n" } else { "" }
    $isAgent = $repoShort -eq "hermes-agent"
    $leaderboardHtml += @"
<h2>$repoShort Leaderboard</h2>
<div class="collapsible-table leaderboard$collapsedClass" id="lb-$repoShort" data-collapse-mode="$collapseMode">
<table>
  <thead><tr><th>Rank</th><th>Contributor</th><th>Credited</th><th>Open</th><th>Rate</th><th>Status</th></tr></thead>
  <tbody>
$leaderboardRows  </tbody>
</table>
$overlayHtml
</div>
$projectionsHtml

"@
}

Export-ClassificationCache -Path $CacheFile

Write-Host "`nFetching representative PRs from $ReadmeRepo README..." -ForegroundColor DarkGray
$readmeB64 = gh api "repos/$ReadmeRepo/contents/README.md" --jq '.content' 2>$null
$readmeText = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($readmeB64 -replace "\s","")))

$representativeHtml = ""
$inBlock = $false; $mdLines = @()
foreach ($line in ($readmeText -split "`n")) {
    if ($line -match "^Representative merged PRs:") { $inBlock = $true; continue }
    if ($inBlock) {
        if ($line -match "^##" -or ($line -notmatch "^-" -and $mdLines.Count -gt 0 -and $line -notmatch "^\s")) { break }
        if ($line -match "^-\s*\[#(\d+)\]\(([^)]+)\)") {
            $prNum = $Matches[1]; $prUrl = $Matches[2]
            $desc = $line -replace "^-\s*\[#\d+\]\([^)]+\)\s*", ""
            $desc = $desc -replace '^\W+\s*', ''
            $rel = ""
            if ($desc -match "\((\[?v[\d.]+\]?(?:\([^)]+\))?)\)\s*$") {
                $rel = $Matches[1] -replace '\[([^\]]+)\]\([^)]+\)', '$1'
                $desc = $desc -replace "\s*\(\[?v[\d.]+\]?(?:\([^)]+\))?\)\s*$", ""
            }
            $desc = $desc -replace '\[([^\]]+)\]\(([^)]+)\)', '<a href="$2">$1</a>'
            $desc = $desc.TrimEnd()
            $mdLines += @{ num = $prNum; url = $prUrl; desc = $desc; release = $rel }
        }
    }
}

if ($mdLines.Count -gt 0) {
    $representativeHtml = @"
<h2>Representative PRs</h2>
<table>
  <tr><th>PR</th><th>Description</th><th>Release</th></tr>

"@
    foreach ($m in $mdLines) {
        $repoLabel = if ($m.url -match "hermes-agent") { "agent" } else { "webui" }
        $relCell = if ($m.release) { $m.release } else { "pending" }
        $representativeHtml += "  <tr><td><a href=`"$($m.url)`">#$($m.num)</a> <span class=`"dim`">$repoLabel</span></td><td>$($m.desc)</td><td>$relCell</td></tr>`n"
    }
    $representativeHtml += "</table>"
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
            $statusClass = "tag-rejected"
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
        repoLabel = if ($pr.repo -match "hermes-agent") { "agent" } else { "webui" }
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

$prStatusFilters = @(
    [pscustomobject][ordered]@{ key = "all"; label = "All"; count = $allPRItems.Count },
    [pscustomobject][ordered]@{ key = "shipped"; label = "Shipped"; count = $totalAccepted },
    [pscustomobject][ordered]@{ key = "open"; label = "Open"; count = $open.Count },
    [pscustomobject][ordered]@{ key = "lost"; label = "Lost"; count = $lost.Count },
    [pscustomobject][ordered]@{ key = "withdrawn"; label = "Withdrawn"; count = $withdrawn.Count }
) | Where-Object { $_.key -eq "all" -or $_.count -gt 0 }

$prFilterPills = ""
foreach ($filter in $prStatusFilters) {
    $activeClass = if ($filter.key -eq "shipped") { " active" } else { "" }
    $prFilterPills += "    <div class=`"sort-pill$activeClass`" data-status=`"$($filter.key)`">$($filter.label) ($($filter.count))</div>`n"
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

$repoSections = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    $repoPRs = @($allPRs | Where-Object { $_.repo -eq $repo })
    $repoOpen = @($repoPRs | Where-Object { $_.state -eq "OPEN" }).Count
    $repoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count + @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $repoLost = @($lost | Where-Object { $_.repo -eq $repo }).Count
    $repoWithdrawn = @($withdrawn | Where-Object { $_.repo -eq $repo }).Count
    $repoRejected = @($rejected | Where-Object { $_.repo -eq $repo }).Count

    $repoSections += @"
<h2>$repoShort ($($repoPRs.Count) PRs)</h2>
<table>
  <tr><th>Status</th><th>Count</th><th>Details</th></tr>
  <tr><td><span class="tag tag-shipped">Shipped</span></td><td>$repoShipped</td><td>Verified via merged release PR, maintainer release evidence, or indirect accepted sibling</td></tr>
  <tr><td><span class="tag tag-open">Open</span></td><td>$repoOpen</td><td>Awaiting maintainer review</td></tr>
$(if ($repoLost -gt 0) { "  <tr><td><span class=`"tag tag-rejected`">Lost</span></td><td>$repoLost</td><td>Competing PR won</td></tr>`n" })$(if ($repoWithdrawn -gt 0) { "  <tr><td><span class=`"tag tag-withdrawn`">Withdrawn</span></td><td>$repoWithdrawn</td><td>Closed without maintainer action</td></tr>`n" })</table>

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

$barShipped = [math]::Round(($totalAccepted / $allPRs.Count) * 100, 1)
$barLost = [math]::Round(($lost.Count / $allPRs.Count) * 100, 1)
$barWithdrawn = [math]::Round(($withdrawn.Count / $allPRs.Count) * 100, 1)
$barOpen = [math]::Round(($open.Count / $allPRs.Count) * 100, 1)

$html = @"
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Open Source Contributions</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="darkreader-lock" />
    <meta name="description" content="150+ PRs across OSS AI projects: provider infrastructure, agent UX, reliability, streaming, and release-linked production work." />
    <meta property="og:title" content="Open Source Contributions">
    <meta property="og:description" content="150+ PRs across OSS AI projects: provider infrastructure, agent UX, reliability, streaming, and release-linked production work.">
    <meta property="og:image" content="https://basedin.nyc/pr-stats/thumb.jpg">
    <meta property="og:url" content="https://basedin.nyc/pr-stats">
    <meta property="og:type" content="website">
    <meta name="darkreader-lock" />
    <meta name="color-scheme" content="light dark" />
    <link rel="stylesheet" href="../style.css?v=20260608a">
  </head>
<body class="pr">

<div class="top-row">
  <h1><a class="back-link" href="../"><svg viewBox="0 0 16 16" width="1em" height="1em"><path d="M10 2L4 8l6 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>Open Source Contributions</h1>
  <nav class="nav-links">
    <a href="../projects/">Projects</a>
    <span class="nav-sep">/</span>
    <span class="current">Stats</span>
    <span class="nav-sep">/</span>
    <a href="../pr-targets/">Targets</a>
    <span class="nav-sep">/</span>
    <a href="https://github.com/rodboev/pr-sweep">Repo</a> <span class="private">(private)</span>
  </nav>
</div>
<p class="subtitle">$Author contributions to nesquena/hermes-webui + NousResearch/hermes-agent</p>

<div class="grid">
  <div class="stat-card"><div class="number">$($allPRs.Count)</div><div class="label">Total PRs</div></div>
  <div class="stat-card"><div class="number green">$totalAccepted</div><div class="label">Shipped</div></div>
  <div class="stat-card"><div class="number yellow">$($open.Count)</div><div class="label">Open</div></div>
  <div class="stat-card"><div class="number">$totalLostWithdrawn</div><div class="label">Lost/Withdrawn</div></div>
</div>
<div class="grid">
  <div class="stat-card"><div class="number green">${acceptanceRate}%</div><div class="label">Acceptance rate ($totalAccepted shipped, $totalLostWithdrawn lost/withdrawn out of $totalClosed closed PRs)</div></div>
  <div class="stat-card"><div class="number blue">$timeSpan</div><div class="label">Time span ($timeRange)</div></div>
</div>

<h2>Breakdown</h2>

<div class="bar-container">
  <div class="bar-segment bar-shipped" data-width="${barShipped}"$(if ($barShipped -gt 4) { " title=`"$totalAccepted`"" })>$(if ($barShipped -gt 4) { $totalAccepted })</div>
  <div class="bar-segment bar-lost" data-width="${barLost}">$(if ($barLost -gt 4) { $lost.Count })</div>
  <div class="bar-segment bar-withdrawn" data-width="${barWithdrawn}">$(if ($barWithdrawn -gt 4) { $withdrawn.Count })</div>
  <div class="bar-segment bar-open" data-width="${barOpen}" title="$($open.Count)">$($open.Count)</div>
</div>
<div class="legend">
  <div class="legend-item"><div class="legend-dot legend-dot-shipped"></div> Shipped ($totalAccepted)</div>
  <div class="legend-item"><div class="legend-dot legend-dot-lost"></div> Lost ($($lost.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-withdrawn"></div> Withdrawn ($($withdrawn.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-open"></div> Open ($($open.Count))</div>
</div>

$repoSections

$leaderboardHtml

$representativeHtml

<div class="landscape-row">
  <h2>PRs</h2>
  <div class="sort-pills" id="pr-filter-pills">
$prFilterPills  </div>
</div>
<table class="targets-table pr-list-table" id="pr-list-table">
  <thead><tr><th>PR</th><th>Repo</th><th>Status</th><th>Date</th><th>Release</th><th>Via</th></tr></thead>
  <tbody id="pr-list-body"></tbody>
</table>

<h2>Methodology</h2>
<div class="section">
  <p>Both repos use a cherry-pick workflow: the maintainer picks commits and closes the PR without GitHub's merge button, so <code>mergedAt</code> is usually null on the original author PR. "Shipped" is determined first from GitHub timeline evidence such as a merged release PR closing or referencing the author PR, then from maintainer release comments when timeline evidence is absent.</p>
  <p>PRs classified as "withdrawn" were either explicitly withdrawn by the author or closed without meaningful maintainer interaction beyond automated bot reviews (Greptile). "Lost" means a competing PR addressing the same issue was accepted instead without explicit credit back to this PR or author.</p>
  <p>The PR table is filterable by status and sorted newest-first, using close time for closed PRs and creation time for open PRs.</p>
  <p>"Rate" is the same for everyone: PRs opened since June 1 divided by days elapsed since June 1. "Credited" is not the same for everyone: most contributors use raw closed + merged PR counts as a proxy, while $Author uses the evidence-based shipped classification from this page.</p>
</div>

<p class="footer">Generated $dateStr from GitHub API. Source: <a href="https://github.com/$ReadmeRepo">$ReadmeRepo</a></p>

<script>
var PR_FILTERS = $prFiltersJson;
var PR_DATA = $prDataJson;

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
function syncLandscapeStickyOffset() {
  var row = document.querySelector('.landscape-row');
  if (!row) return;
  document.body.style.setProperty('--landscape-row-offset', row.getBoundingClientRect().height + 'px');
}
function renderPrTable(statusKey) {
  var tbody = document.getElementById('pr-list-body');
  if (!tbody) return;
  var filtered = PR_DATA.filter(function(item) {
    return statusKey === 'all' || item.statusKey === statusKey;
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
      '<td>' + item.dateLabel + '</td>' +
      '<td>' + (item.releaseLabel || '') + '</td>' +
      '<td>' + via + '</td>' +
    '</tr>' +
    '<tr class="pr-title-row">' +
      '<td></td>' +
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
  var statusKey = pill.getAttribute('data-status');
  document.querySelectorAll('#pr-filter-pills .sort-pill').forEach(function(p) {
    p.classList.remove('active');
  });
  pill.classList.add('active');
  renderPrTable(statusKey);
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
renderPrTable('shipped');
updateCollapsedOverlays();
document.addEventListener('scroll', updateCollapsedOverlays, { passive: true });
</script>

</body>
</html>
"@

$html | Out-File -FilePath $OutFile -Encoding utf8
Write-Host "`nWritten to $OutFile" -ForegroundColor Green
Write-Host "  Total: $($allPRs.Count) | Shipped: $totalAccepted | Open: $($open.Count) | Lost: $($lost.Count) | Rate: ${acceptanceRate}%"
Write-Host "  Closed classification cache hits: $script:ClassificationCacheHits | Leaderboard cache hits: $script:LeaderboardCacheHits | Cache file: $CacheFile" -ForegroundColor DarkGray

if ($OpenOutput) {
    Start-Process $OutFile
}
