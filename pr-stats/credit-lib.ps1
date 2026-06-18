# Dot-sourced by generate.ps1 — release-attribution credit maps and contributor discovery.

$script:RepoCreditProfile = @{
    "nesquena/hermes-webui" = "changelog-release"
    "kenn-io/agentsview" = "github-evidence"
    "thedotmack/claude-mem" = "github-evidence"
}

$script:RepoContributorsSeedBranch = @{
    "nesquena/hermes-webui" = "master"
}

$script:WebuiCommitScanMaxPages = 15
$script:WebuiMergedScanMaxPages = 50
$script:ContributorsMdSeedCacheHours = 24 * 7

function Get-RepoCreditProfile([string]$Repo) {
    if ($script:RepoCreditProfile.ContainsKey($Repo)) {
        return $script:RepoCreditProfile[$Repo]
    }
    return "github-evidence"
}

function Get-ContributorsMdRawUrl([string]$Repo) {
    if (-not $script:RepoContributorsSeedBranch.ContainsKey($Repo)) {
        return $null
    }
    $branch = $script:RepoContributorsSeedBranch[$Repo]
    return "https://raw.githubusercontent.com/$Repo/$branch/CONTRIBUTORS.md"
}

function Get-CachedContributorsMdSeed([string]$Repo) {
    if (-not $script:ClassificationCache.contributorsMdSeeds) {
        return $null
    }
    if (-not $script:ClassificationCache.contributorsMdSeeds.ContainsKey($Repo)) {
        return $null
    }
    $entry = $script:ClassificationCache.contributorsMdSeeds[$Repo]
    if (-not $entry -or -not $entry.cachedAt -or -not $entry.logins) {
        return $null
    }
    try {
        $cachedAt = [datetime]$entry.cachedAt
    } catch {
        return $null
    }
    if (((Get-Date) - $cachedAt).TotalHours -gt $script:ContributorsMdSeedCacheHours) {
        return $null
    }
    return @($entry.logins | ForEach-Object { [string]$_ })
}

function Set-CachedContributorsMdSeed([string]$Repo, [string[]]$Logins) {
    if (-not $script:ClassificationCache.contributorsMdSeeds) {
        $script:ClassificationCache.contributorsMdSeeds = @{}
    }
    $script:ClassificationCache.contributorsMdSeeds[$Repo] = @{
        cachedAt = (Get-Date).ToString("o")
        logins = @($Logins | Where-Object { $_ } | Select-Object -Unique)
    }
}

function Get-ContributorsMdSeedLogins([string]$Repo, [hashtable]$Exclusions) {
    $cached = Get-CachedContributorsMdSeed -Repo $Repo
    if ($cached) {
        return @($cached | Where-Object { -not (Test-IsLeaderboardExcludedLogin -Login $_ -Exclusions $Exclusions) })
    }

    $url = Get-ContributorsMdRawUrl -Repo $Repo
    if (-not $url) {
        return @()
    }

    $text = $null
    try {
        $text = (Invoke-WebRequest -Uri $url -UseBasicParsing).Content
    } catch {
        Write-ProgressHost "    CONTRIBUTORS.md seed fetch failed for $Repo" -ForegroundColor Yellow
        return @()
    }

    $logins = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($match in [regex]::Matches($text, '\[@([\w-]+)\]\(https://github\.com/\1(?:\)|/)')) {
        [void]$logins.Add($match.Groups[1].Value)
    }
    foreach ($match in [regex]::Matches($text, '\[@([\w-]+)\]\(https://github\.com/[\w-]+(?:\)|/)')) {
        [void]$logins.Add($match.Groups[1].Value)
    }

    $seed = @($logins | Where-Object { $_ -and -not (Test-IsLeaderboardExcludedLogin -Login $_ -Exclusions $Exclusions) })
    if ($seed.Count -gt 0) {
        Set-CachedContributorsMdSeed -Repo $Repo -Logins $seed
    }
    return $seed
}

function Merge-CommunityContributorLogins(
    [string]$Repo,
    [object[]]$RecentRepoPRs,
    [hashtable]$Exclusions,
    [string[]]$PriorLogins
) {
    $recent = @(Get-CommunityContributorLogins -Repo $Repo -RecentRepoPRs $RecentRepoPRs -Exclusions $Exclusions)
    $seed = @(Get-ContributorsMdSeedLogins -Repo $Repo -Exclusions $Exclusions)
    $merged = @($PriorLogins + $seed + $recent | Where-Object { $_ } | Select-Object -Unique)
    return @($merged | Where-Object { -not (Test-IsLeaderboardExcludedLogin -Login $_ -Exclusions $Exclusions) })
}

function Get-RemoteRepoFileSha([string]$Repo, [string]$Path) {
    $raw = Invoke-Gh api "repos/$Repo/contents/$Path" --jq '.sha' 2>$null
    if ($raw) { return $raw.Trim() }
    return ""
}

function Get-RemoteRepoFileText([string]$Repo, [string]$Path) {
    $metaRaw = Invoke-Gh api "repos/$Repo/contents/$Path" --jq '{sha: .sha, size: .size, download_url: .download_url, content: .content, encoding: .encoding}' 2>$null
    if (-not $metaRaw) {
        return @{ Text = ""; Sha = "" }
    }
    $meta = $metaRaw | ConvertFrom-Json
    $sha = [string]$meta.sha
    if ($meta.download_url -and [int]$meta.size -gt 900000) {
        try {
            $text = (Invoke-WebRequest -Uri $meta.download_url -UseBasicParsing).Content
            return @{ Text = $text; Sha = $sha }
        } catch {
            return @{ Text = ""; Sha = $sha }
        }
    }
    if ($meta.content) {
        $bytes = [Convert]::FromBase64String(($meta.content -replace '\s', ''))
        return @{ Text = [Text.Encoding]::UTF8.GetString($bytes); Sha = $sha }
    }
    return @{ Text = ""; Sha = $sha }
}

function New-EmptyCreditMap {
    return @{}
}

function Add-CreditPair([hashtable]$Map, [string]$Login, [int]$Number) {
    if (-not $Login -or $Number -le 0) { return }
    if (-not $Map.ContainsKey($Login)) {
        $Map[$Login] = [System.Collections.Generic.HashSet[int]]::new()
    }
    [void]$Map[$Login].Add($Number)
}

function Get-WebuiChangelogCreditMap([string]$Text) {
    $map = New-EmptyCreditMap
    if (-not $Text) { return $map }

    foreach ($match in [regex]::Matches($Text, '(?im)-\s*\*\*PR #(\d+)\*\* by @([\w-]+)')) {
        Add-CreditPair -Map $map -Login $match.Groups[2].Value -Number ([int]$match.Groups[1].Value)
    }
    foreach ($match in [regex]::Matches($Text, '(?im)\*\*PR #(\d+)\*\* by @([\w-]+)')) {
        Add-CreditPair -Map $map -Login $match.Groups[2].Value -Number ([int]$match.Groups[1].Value)
    }
    foreach ($match in [regex]::Matches($Text, '(?im)PR #(\d+) by @([\w-]+)')) {
        Add-CreditPair -Map $map -Login $match.Groups[2].Value -Number ([int]$match.Groups[1].Value)
    }
    foreach ($match in [regex]::Matches($Text, '(?im)@([\w-]+)\s*[—–-]\s*PR #(\d+)')) {
        Add-CreditPair -Map $map -Login $match.Groups[1].Value -Number ([int]$match.Groups[2].Value)
    }
    foreach ($match in [regex]::Matches($Text, '(?im)\(credit:\s*@([\w-]+)\)[^\n]{0,240}?PR #(\d+)')) {
        Add-CreditPair -Map $map -Login $match.Groups[1].Value -Number ([int]$match.Groups[2].Value)
    }
    foreach ($match in [regex]::Matches($Text, '(?im)PR #(\d+)[^\n]{0,240}?\(credit:\s*@([\w-]+)\)')) {
        Add-CreditPair -Map $map -Login $match.Groups[2].Value -Number ([int]$match.Groups[1].Value)
    }
    foreach ($match in [regex]::Matches($Text, '(?im)\((#[\d\s/]+),\s*@([\w-]+)\)')) {
        $login = $match.Groups[2].Value
        foreach ($prMatch in [regex]::Matches($match.Groups[1].Value, '#(\d+)')) {
            Add-CreditPair -Map $map -Login $login -Number ([int]$prMatch.Groups[1].Value)
        }
    }

    return $map
}

function Get-GithubLoginFromCoAuthorTrailer([string]$TrailerLine) {
    if (-not $TrailerLine) { return $null }
    $emailMatch = [regex]::Match($TrailerLine, '([\w-]+(?:\+[\w-]+)?@users\.noreply\.github\.com)', 'IgnoreCase')
    if ($emailMatch.Success) {
        $email = $emailMatch.Groups[1].Value
        $local = ($email -split '@')[0]
        if ($local -match '^\d+\+(.+)$') {
            return $Matches[1]
        }
        return $local
    }
    return $null
}

function Get-GithubRestJson([string]$Path) {
    $token = (Invoke-Gh auth token 2>$null)
    if (-not $token) { return $null }
    $token = $token.Trim()
    try {
        return Invoke-RestMethod -Uri "https://api.github.com/$Path" -Headers @{
            Authorization = "Bearer $token"
            Accept = "application/vnd.github+json"
            'User-Agent' = "basedin-pr-stats"
        }
    } catch {
        return $null
    }
}

function Get-WebuiFilteredMergedPrCreditMap([string]$Repo, [hashtable]$Exclusions) {
    $map = New-EmptyCreditMap
    $cursor = $null
    $page = 0
    $owner = $Repo.Split('/')[0]
    $name = $Repo.Split('/')[1]

    while ($page -lt $script:WebuiMergedScanMaxPages) {
        if ($cursor) {
            $query = @"
query(`$cursor: String) {
  repository(owner: "$owner", name: "$name") {
    pullRequests(states: MERGED, first: 100, after: `$cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number title author { login } }
    }
  }
}
"@
        } else {
            $query = @"
query {
  repository(owner: "$owner", name: "$name") {
    pullRequests(states: MERGED, first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number title author { login } }
    }
  }
}
"@
        }

        $result = $null
        try {
            if ($cursor) {
                $raw = Invoke-Gh api graphql -f query=$query -f cursor=$cursor
            } else {
                $raw = Invoke-Gh api graphql -f query=$query
            }
            if ($raw) {
                $result = $raw | ConvertFrom-Json
            }
        } catch {
            break
        }

        if (-not $result -or -not $result.data -or -not $result.data.repository -or -not $result.data.repository.pullRequests) {
            break
        }

        $connection = $result.data.repository.pullRequests
        foreach ($node in @($connection.nodes)) {
            $login = [string]$node.author.login
            if (-not $login) { continue }
            if (Test-IsLeaderboardExcludedLogin -Login $login -Exclusions $Exclusions) { continue }
            if (Test-IsReleaseTitle -Title $node.title) { continue }
            Add-CreditPair -Map $map -Login $login -Number ([int]$node.number)
        }

        $page++
        if (-not $connection.pageInfo.hasNextPage) {
            break
        }
        $cursor = $connection.pageInfo.endCursor
    }

    return $map
}

function Get-WebuiCommitCreditMap([string]$Repo) {
    $authorCache = $script:ClassificationCache.prAuthorsByNumber
    $map = New-EmptyCreditMap
    for ($page = 1; $page -le $script:WebuiCommitScanMaxPages; $page++) {
        $commits = $null
        try {
            $commits = @(Get-GithubRestJson -Path "repos/$Repo/commits?per_page=100&page=$page")
        } catch {
            break
        }
        if (-not $commits -or $commits.Count -eq 0) {
            break
        }

        foreach ($commit in $commits) {
            $message = [string]$commit.commit.message
            if ($message -notmatch '(?i)Co-authored-by:|release:|contributor batch|salvaged') {
                continue
            }

            $prNumbers = @([regex]::Matches($message, '#(\d+)') | ForEach-Object { [int]$_.Groups[1].Value } | Select-Object -Unique)
            if ($prNumbers.Count -eq 0) {
                continue
            }

            $coAuthors = @()
            foreach ($line in ($message -split "`n")) {
                if ($line -notmatch '(?i)^Co-authored-by:') { continue }
                $login = Get-GithubLoginFromCoAuthorTrailer -TrailerLine $line
                if ($login) { $coAuthors += $login }
            }
            if ($coAuthors.Count -eq 0) {
                continue
            }

            foreach ($prNum in $prNumbers) {
                $cacheKey = "$Repo#$prNum"
                if (-not $authorCache.ContainsKey($cacheKey)) {
                    $pr = Get-PullRequestState -Repo $Repo -Number $prNum -Quiet
                    $authorCache[$cacheKey] = if ($pr) { [string](Get-ScalarValue $pr.author.login) } else { "" }
                }
                $authorLogin = [string]$authorCache[$cacheKey]
                if (-not $authorLogin) { continue }
                if ($coAuthors -contains $authorLogin) {
                    Add-CreditPair -Map $map -Login $authorLogin -Number $prNum
                }
            }
        }
    }
    return $map
}

function Merge-CreditMaps([hashtable[]]$Maps) {
    $merged = New-EmptyCreditMap
    foreach ($map in $Maps) {
        foreach ($login in $map.Keys) {
            foreach ($num in $map[$login]) {
                Add-CreditPair -Map $merged -Login $login -Number $num
            }
        }
    }
    return $merged
}

function ConvertTo-CreditCountMap([hashtable]$CreditMap) {
    $counts = @{}
    foreach ($login in $CreditMap.Keys) {
        $counts[$login] = $CreditMap[$login].Count
    }
    return $counts
}

function Get-CachedReleaseCreditMeta([string]$Repo, [Nullable[datetime]]$StartDate) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    if (-not $script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        return $null
    }
    $entry = $script:ClassificationCache.leaderboards[$cacheKey]
    if (-not $entry.releaseCreditMeta) {
        return $null
    }
    return $entry.releaseCreditMeta
}

function Set-CachedReleaseCreditData(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [hashtable]$Counts,
    [hashtable]$Meta
) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    if (-not $script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        $script:ClassificationCache.leaderboards[$cacheKey] = @{
            cachedAt = (Get-Date).ToString("o")
            refreshedAt = $null
            logins = @()
            stats = @{}
            shippedCounts = @{}
            releaseCreditCounts = @{}
            releaseCreditMeta = $null
        }
    }
    $stored = [ordered]@{}
    foreach ($login in ($Counts.Keys | Sort-Object)) {
        $stored[$login] = [int]$Counts[$login]
    }
    $script:ClassificationCache.leaderboards[$cacheKey].releaseCreditCounts = $stored
    $script:ClassificationCache.leaderboards[$cacheKey].releaseCreditMeta = $Meta
}

function Get-ChangelogReleaseCreditCounts(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [hashtable]$Exclusions,
    [switch]$ForceRefresh
) {
    $cachedMeta = Get-CachedReleaseCreditMeta -Repo $Repo -StartDate $StartDate
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    $cachedCounts = @{}
    if ($script:ClassificationCache.leaderboards.ContainsKey($cacheKey) -and $script:ClassificationCache.leaderboards[$cacheKey].releaseCreditCounts) {
        $rawCounts = $script:ClassificationCache.leaderboards[$cacheKey].releaseCreditCounts
        if ($rawCounts -is [hashtable]) {
            foreach ($key in $rawCounts.Keys) {
                $cachedCounts[$key] = [int]$rawCounts[$key]
            }
        } else {
            foreach ($prop in $rawCounts.PSObject.Properties) {
                $cachedCounts[$prop.Name] = [int]$prop.Value
            }
        }
    }

    if (-not $ForceRefresh -and $cachedMeta -and $cachedCounts.Count -gt 0) {
        $currentSha = Get-RemoteRepoFileSha -Repo $Repo -Path "CHANGELOG.md"
        if ($currentSha -and $cachedMeta.changelogSha -eq $currentSha) {
            return $cachedCounts
        }
    }

    $changelog = Get-RemoteRepoFileText -Repo $Repo -Path "CHANGELOG.md"
    if (-not $changelog.Text) {
        if ($cachedCounts.Count -gt 0) { return $cachedCounts }
        return @{}
    }

    Write-ProgressHost "    Building release credit map from CHANGELOG + contributor merges + release commits..." -ForegroundColor DarkGray
    $changelogMap = Get-WebuiChangelogCreditMap -Text $changelog.Text
    $mergedMap = Get-WebuiFilteredMergedPrCreditMap -Repo $Repo -Exclusions $Exclusions
    $commitMap = Get-WebuiCommitCreditMap -Repo $Repo
    $unionMap = Merge-CreditMaps @($changelogMap, $mergedMap, $commitMap)
    $counts = ConvertTo-CreditCountMap -CreditMap $unionMap

    Set-CachedReleaseCreditData -Repo $Repo -StartDate $StartDate -Counts $counts -Meta @{
        changelogSha = $changelog.Sha
        builtAt = (Get-Date).ToString("o")
        pairCount = ($unionMap.Values | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum
    }

    return $counts
}

function Get-AuthorMergedPrCount([string]$Repo, [string]$Login) {
    $data = $null
    try {
        $result = Invoke-Gh api graphql -f query="query { merged: search(query: `"repo:$Repo is:pr author:$Login is:merged`", type: ISSUE, first: 1) { issueCount } }" 2>$null | ConvertFrom-Json
        $data = $result.data
    } catch {}
    if ($data) {
        $count = Get-PropertyIssueCount -Data $data -Name "merged"
        if ($null -ne $count) { return [int]$count }
    }
    return $null
}

function Apply-LeaderboardCreditedCounts(
    [string]$Repo,
    [hashtable]$CommunityStats,
    [string]$Author,
    [int]$MyRepoCredited,
    [hashtable]$CachedShippedCounts,
    [hashtable]$Exclusions,
    [switch]$UseCachedShipped,
    [Nullable[datetime]]$StartDate,
    [int]$ClassifyTop,
    [switch]$ForceRefresh
) {
    $profile = Get-RepoCreditProfile -Repo $Repo
    $shippedCountsToSave = @{}
    $classifiedFromCache = 0
    $classifiedLive = 0

    if ($profile -eq "changelog-release") {
        # Release credits rebuild only when CHANGELOG.md SHA changes, not on leaderboard refresh.
        $creditCounts = Get-ChangelogReleaseCreditCounts -Repo $Repo -StartDate $StartDate -Exclusions $Exclusions
        foreach ($login in $CommunityStats.Keys) {
            $count = if ($creditCounts.ContainsKey($login)) { [int]$creditCounts[$login] } else { 0 }
            $shippedCountsToSave[$login] = $count
            $CommunityStats[$login].credited = $count
            $CommunityStats[$login].estimated = $false
            $CommunityStats[$login].shippedClassified = $true
        }
        return @{
            ShippedCountsToSave = $shippedCountsToSave
            ClassifiedFromCache = 0
            ClassifiedLive = $CommunityStats.Count
        }
    }

    $classifyLogins = @(Get-TopCreditedLogins -Stats $CommunityStats -Top $ClassifyTop)
    foreach ($login in $CommunityStats.Keys) {
        if ($UseCachedShipped -and -not $ForceRefresh -and $CachedShippedCounts.ContainsKey($login)) {
            $count = [int]$CachedShippedCounts[$login]
            $classifiedFromCache++
            if ($classifyLogins -contains $login) {
                $CommunityStats[$login].estimated = $false
                $CommunityStats[$login].shippedClassified = $true
            } else {
                $CommunityStats[$login].estimated = $true
                $CommunityStats[$login].shippedClassified = $false
            }
        } elseif ($classifyLogins -contains $login) {
            if ($login -eq $Author) {
                $count = $MyRepoCredited
            } else {
                $count = Get-ContributorShippedCount -Repo $Repo -Login $login
                $classifiedLive++
                Write-Host "      $login shipped=$count (classified)" -ForegroundColor DarkGray
            }
            $CommunityStats[$login].estimated = $false
            $CommunityStats[$login].shippedClassified = $true
        } else {
            $count = Get-AuthorMergedPrCount -Repo $Repo -Login $login
            $classifiedLive++
            $CommunityStats[$login].estimated = $true
            $CommunityStats[$login].shippedClassified = $false
        }

        if ($null -eq $count) {
            $count = [int]$CommunityStats[$login].credited
        }
        $shippedCountsToSave[$login] = $count
        $CommunityStats[$login].credited = $count
    }

    return @{
        ShippedCountsToSave = $shippedCountsToSave
        ClassifiedFromCache = $classifiedFromCache
        ClassifiedLive = $classifiedLive
    }
}
