# Dot-sourced by generate.ps1 — release-attribution credit maps and contributor discovery.

$script:RepoCreditProfile = @{
    "nesquena/hermes-webui" = "changelog-release"
    "kenn-io/agentsview" = "github-evidence"
    "thedotmack/claude-mem" = "github-evidence"
}

$script:RepoContributorsSeedBranch = @{
    "nesquena/hermes-webui" = "master"
}

$script:WebuiCommitScanMaxPages = 40
$script:WebuiAbsorbCommitScanMaxPages = 80
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

function Get-ContributorsMdRankedCredits([string]$Repo, [hashtable]$Exclusions) {
    $url = Get-ContributorsMdRawUrl -Repo $Repo
    if (-not $url) { return @() }
    $text = $null
    try {
        $text = (Invoke-WebRequest -Uri $url -UseBasicParsing).Content
    } catch {
        return @()
    }
    $results = @()
    foreach ($match in [regex]::Matches($text, '\|\s*\d+\s*\|\s*\[@([\w-]+)\]\([^)]+\)\s*\|\s*(\d+)\s*\|')) {
        $login = $match.Groups[1].Value
        $count = [int]$match.Groups[2].Value
        if (-not (Test-IsLeaderboardExcludedLogin -Login $login -Exclusions $Exclusions)) {
            $results += [pscustomobject]@{ Login = $login; Count = $count }
        }
    }
    return $results
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

function Get-WebuiReleaseAbsorbedCreditMap(
    [string]$Text,
    [string]$Repo,
    [hashtable]$Exclusions
) {
    $map = New-EmptyCreditMap
    if (-not $Text) { return $map }

    $candidateNumbers = [System.Collections.Generic.HashSet[int]]::new()
    $attributedNumbers = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($match in [regex]::Matches($Text, '\(#([\d\s/,#]+),\s*@[\w-]+\)')) {
        foreach ($prMatch in [regex]::Matches($match.Groups[1].Value, '(\d+)')) {
            [void]$attributedNumbers.Add([int]$prMatch.Groups[1].Value)
        }
    }
    $sections = [regex]::Split($Text, '(?m)^## \[v')
    for ($i = 1; $i -lt $sections.Count; $i++) {
        $section = $sections[$i]
        foreach ($match in [regex]::Matches($section, '\(#(\d+(?:\s*[,/]\s*#\d+)*)\)')) {
            foreach ($prMatch in [regex]::Matches($match.Value, '#(\d+)')) {
                $num = [int]$prMatch.Groups[1].Value
                if (-not $attributedNumbers.Contains($num)) {
                    [void]$candidateNumbers.Add($num)
                }
            }
        }
    }

    foreach ($num in @($candidateNumbers)) {
        $pr = Get-PullRequestState -Repo $Repo -Number $num -Quiet
        if (-not $pr) { continue }
        $state = [string]$pr.state
        if ($state -eq "MERGED") { continue }
        if ($state -ne "CLOSED") { continue }
        $authorLogin = Get-PullRequestAuthorLogin -Repo $Repo -Number $num
        if (-not $authorLogin) { continue }
        if (Test-IsLeaderboardExcludedLogin -Login $authorLogin -Exclusions $Exclusions) { continue }
        if (Test-IsMaintainerReleaseVehiclePullRequest -Repo $Repo -Number $num -AuthorLogin $authorLogin -Title ([string]$pr.title) -Exclusions $Exclusions) { continue }
        Add-CreditPair -Map $map -Login $authorLogin -Number $num
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
            $query = "query(`$cursor: String) { repository(owner: `"$owner`", name: `"$name`") { pullRequests(states: MERGED, first: 100, after: `$cursor, orderBy: {field: UPDATED_AT, direction: DESC}) { pageInfo { hasNextPage endCursor } nodes { number title author { login } } } } }"
        } else {
            $query = "query { repository(owner: `"$owner`", name: `"$name`") { pullRequests(states: MERGED, first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) { pageInfo { hasNextPage endCursor } nodes { number title author { login } } } } }"
        }

        $result = $null
        try {
            if ($cursor) {
                $raw = Invoke-Gh api graphql -f query=$query -f cursor=$cursor 2>$null
            } else {
                $raw = Invoke-Gh api graphql -f query=$query 2>$null
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
            if (Test-IsMaintainerReleaseVehiclePullRequest -Repo $Repo -Number ([int]$node.number) -AuthorLogin $login -Title ([string]$node.title) -Exclusions $Exclusions) { continue }
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

function Get-WebuiMasterCommitScan([string]$Repo, [int]$MaxPages = $script:WebuiAbsorbCommitScanMaxPages) {
    $allCommits = @()
    $coAuthorIndex = @{}
    $subjectPrNumbers = @{}
    for ($page = 1; $page -le $MaxPages; $page++) {
        $commits = $null
        try {
            $commits = @(Get-GithubRestJson -Path "repos/$Repo/commits?per_page=100&page=$page")
        } catch {
            break
        }
        if (-not $commits -or $commits.Count -eq 0) {
            break
        }
        $allCommits += $commits

        foreach ($commit in $commits) {
            $message = [string]$commit.commit.message
            $parentCount = if ($commit.parents) { @($commit.parents).Count } else { 0 }

            $subject = ($message -split "`n")[0]
            $subjectMatches = [regex]::Matches($subject, '\(#(\d+(?:\s*[,/]\s*#?\d+)*)\)')
            foreach ($m in $subjectMatches) {
                $innerMatches = [regex]::Matches($m.Groups[1].Value, '#?(\d+)')
                foreach ($im in $innerMatches) {
                    $prNum = [int]$im.Groups[1].Value
                    if (-not $subjectPrNumbers.ContainsKey($prNum)) {
                        $subjectPrNumbers[$prNum] = @{ Sha = $commit.sha; ParentCount = $parentCount; HasCoAuthor = ($message -match '(?i)Co-authored-by:') }
                    }
                }
            }

            foreach ($line in ($message -split "`n")) {
                if ($line -notmatch '(?i)^Co-authored-by:') { continue }
                $login = Get-GithubLoginFromCoAuthorTrailer -TrailerLine $line
                if (-not $login) { continue }
                $prMatches = [regex]::Matches($message, '#(\d+)')
                foreach ($pm in $prMatches) {
                    $prNum = [int]$pm.Groups[1].Value
                    if (-not $coAuthorIndex.ContainsKey($prNum)) {
                        $coAuthorIndex[$prNum] = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
                    }
                    $null = $coAuthorIndex[$prNum].Add($login)
                }
            }
        }
    }
    return @{
        Commits = $allCommits
        CoAuthorIndex = $coAuthorIndex
        SubjectPrNumbers = $subjectPrNumbers
        HeadSha = if ($allCommits.Count -gt 0) { $allCommits[0].sha } else { $null }
    }
}

function Get-WebuiCommitCreditMap([string]$Repo, [object]$CommitScan) {
    if (-not $CommitScan) {
        $CommitScan = Get-WebuiMasterCommitScan -Repo $Repo -MaxPages $script:WebuiCommitScanMaxPages
    }
    $map = New-EmptyCreditMap
    foreach ($commit in $CommitScan.Commits) {
        $message = [string]$commit.commit.message
        if ($message -notmatch '(?i)Co-authored-by:|release:|contributor batch|salvaged') {
            continue
        }
        $prNumbers = @([regex]::Matches($message, '#(\d+)') | ForEach-Object { [int]$_.Groups[1].Value } | Select-Object -Unique)
        if ($prNumbers.Count -eq 0) { continue }

        $coAuthors = @()
        foreach ($line in ($message -split "`n")) {
            if ($line -notmatch '(?i)^Co-authored-by:') { continue }
            $login = Get-GithubLoginFromCoAuthorTrailer -TrailerLine $line
            if ($login) { $coAuthors += $login }
        }
        if ($coAuthors.Count -eq 0) { continue }

        foreach ($prNum in $prNumbers) {
            $authorLogin = Get-PullRequestAuthorLogin -Repo $Repo -Number $prNum
            if (-not $authorLogin) { continue }
            if ($coAuthors -contains $authorLogin) {
                Add-CreditPair -Map $map -Login $authorLogin -Number $prNum
            }
        }
    }
    return $map
}

function Get-WebuiAbsorbCommitCreditMap([string]$Repo, [hashtable]$Exclusions, [object]$CommitScan) {
    if (-not $CommitScan) {
        $CommitScan = Get-WebuiMasterCommitScan -Repo $Repo
    }
    if (-not $script:ClassificationCache.absorbCommitMap) {
        $script:ClassificationCache.absorbCommitMap = @{}
    }
    $cached = $script:ClassificationCache.absorbCommitMap[$Repo]
    if ($cached -and $cached.headSha -eq $CommitScan.HeadSha) {
        $map = New-EmptyCreditMap
        foreach ($login in $cached.credits.Keys) {
            $nums = $cached.credits[$login]
            if ($nums -is [System.Collections.Generic.HashSet[int]]) {
                $map[$login] = $nums
            } else {
                $map[$login] = [System.Collections.Generic.HashSet[int]]::new([int[]]@($nums))
            }
        }
        return $map
    }

    $map = New-EmptyCreditMap
    foreach ($prNum in $CommitScan.SubjectPrNumbers.Keys) {
        $info = $CommitScan.SubjectPrNumbers[$prNum]
        if ($info.ParentCount -ne 1) { continue }
        if ($info.HasCoAuthor) { continue }

        $pr = Get-PullRequestState -Repo $Repo -Number $prNum -Quiet
        if (-not $pr) { continue }
        if ([string]$pr.state -ne "CLOSED") { continue }

        $authorLogin = Get-PullRequestAuthorLogin -Repo $Repo -Number $prNum
        if (-not $authorLogin) { continue }
        if (Test-IsLeaderboardExcludedLogin -Login $authorLogin -Exclusions $Exclusions) { continue }

        $title = [string]$pr.title
        if (Test-IsMaintainerReleaseVehiclePullRequest -Repo $Repo -Number $prNum -AuthorLogin $authorLogin -Title $title -Exclusions $Exclusions) { continue }

        Add-CreditPair -Map $map -Login $authorLogin -Number $prNum
    }

    $serializable = @{}
    foreach ($login in $map.Keys) {
        $serializable[$login] = @($map[$login])
    }
    $script:ClassificationCache.absorbCommitMap[$Repo] = @{
        headSha = $CommitScan.HeadSha
        cachedAt = (Get-Date).ToString("o")
        credits = $serializable
    }

    return $map
}

function Invoke-ShipCommentClassifier(
    [int]$PrNumber,
    [string]$CommentBody,
    [string]$PrAuthorLogin,
    [string]$Repo,
    [hashtable]$Exclusions,
    [hashtable]$CoAuthorIndex
) {
    if (-not $CommentBody) { return $null }

    $deflectionPattern = '(?i)\b(?:supersed(?:e|ed|es|ing)|replaced|covered|duplicat(?:e|ed|es|ing)|consolidat(?:e|ed|es|ing)|closed?\s+in\s+favor|closing\s+in\s+favor)\b'
    $prRefPattern = '(?:https://github\.com/[^/\s]+/[^/\s]+/pull/|#)(\d+)'
    $ownShipPattern = '(?i)\b(?:cherry[- ]?pick(?:ed|ing)?|absorbed|salvaged\s+into|merged[- ]?via|commits?\s+carried|carried\s+forward|included|landed|integrated)\b'
    $plainShipPattern = '(?i)\b(?:shipped|released)\b|\bv\d+\.\d+\.\d+'

    $hasDeflection = $CommentBody -match $deflectionPattern
    $refMatches = [regex]::Matches($CommentBody, $prRefPattern)
    $referencedPrs = @($refMatches | ForEach-Object { [int]$_.Groups[1].Value } | Where-Object { $_ -ne $PrNumber } | Select-Object -Unique)

    if ($hasDeflection -and $referencedPrs.Count -gt 0) {
        $hasOwnShip = $CommentBody -match $ownShipPattern
        if ($hasOwnShip) {
            return "own-ship"
        }

        foreach ($supersedingPr in $referencedPrs) {
            $supersedingAuthor = Get-PullRequestAuthorLogin -Repo $Repo -Number $supersedingPr
            if ($supersedingAuthor -eq $PrAuthorLogin) {
                return "own-ship"
            }
            if ($CoAuthorIndex -and $CoAuthorIndex.ContainsKey($supersedingPr) -and $CoAuthorIndex[$supersedingPr].Contains($PrAuthorLogin)) {
                return "co-author-ship"
            }
            try {
                $commits = @(Get-GithubRestJson -Path "repos/$Repo/pulls/$supersedingPr/commits")
                foreach ($c in $commits) {
                    $msg = [string]$c.commit.message
                    if ($msg -match "(?i)Co-authored-by:.*$([regex]::Escape($PrAuthorLogin))") {
                        return "co-author-ship"
                    }
                }
            } catch {}
        }
        return "deflection"
    }

    if ($CommentBody -match $ownShipPattern) {
        return "own-ship"
    }
    if ($CommentBody -match $plainShipPattern) {
        return "plain-ship"
    }

    return $null
}

function Get-WebuiShipCommentCreditMap(
    [string]$Repo,
    [string[]]$Logins,
    [hashtable]$Exclusions,
    [hashtable]$CoAuthorIndex,
    [hashtable]$AlreadyCreditedMap
) {
    if (-not $script:ClassificationCache.shipCommentClassifications) {
        $script:ClassificationCache.shipCommentClassifications = @{}
    }

    $map = New-EmptyCreditMap
    foreach ($login in $Logins) {
        if (Test-IsLeaderboardExcludedLogin -Login $login -Exclusions $Exclusions) { continue }

        $raw = Invoke-Gh pr list --repo $Repo --author $login --state closed --limit 500 --json "number,comments" 2>$null
        if (-not $raw) { continue }
        $closedPrs = @(($raw | ConvertFrom-Json) | ForEach-Object { $_ })

        foreach ($pr in $closedPrs) {
            $prNum = [int]$pr.number
            if ($AlreadyCreditedMap -and $AlreadyCreditedMap.ContainsKey($login) -and $AlreadyCreditedMap[$login].Contains($prNum)) {
                continue
            }

            $cacheKey = "$Repo#$prNum"
            $commentCount = if ($pr.comments) { @($pr.comments).Count } else { 0 }
            $cachedEntry = $script:ClassificationCache.shipCommentClassifications[$cacheKey]
            if ($cachedEntry -and $cachedEntry.commentCount -eq $commentCount) {
                $classification = $cachedEntry.classification
            } else {
                $comments = $null
                try {
                    $comments = @(Get-GithubRestJson -Path "repos/$Repo/issues/$prNum/comments")
                } catch {}
                if (-not $comments) { $comments = @() }

                $bestClassification = $null
                $priorityOrder = @{ "own-ship" = 4; "co-author-ship" = 3; "plain-ship" = 2; "deflection" = 1 }
                foreach ($comment in $comments) {
                    $commentAuthor = if ($comment.user) { [string]$comment.user.login } else { "" }
                    if ($commentAuthor -eq $login) { continue }
                    if ($commentAuthor -match '(?i)greptile') { continue }

                    $result = Invoke-ShipCommentClassifier -PrNumber $prNum -CommentBody ([string]$comment.body) -PrAuthorLogin $login -Repo $Repo -Exclusions $Exclusions -CoAuthorIndex $CoAuthorIndex
                    if ($result -and ($null -eq $bestClassification -or $priorityOrder[$result] -gt $priorityOrder[$bestClassification])) {
                        $bestClassification = $result
                    }
                }

                $classification = if ($bestClassification) { $bestClassification } else { "none" }
                $script:ClassificationCache.shipCommentClassifications[$cacheKey] = @{
                    classification = $classification
                    classifiedAt = (Get-Date).ToString("o")
                    commentCount = $commentCount
                }
            }

            if ($classification -in @("own-ship", "co-author-ship", "plain-ship")) {
                Add-CreditPair -Map $map -Login $login -Number $prNum
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

function Get-PullRequestAuthorLogin([string]$Repo, [int]$Number) {
    if (-not $script:ClassificationCache.prAuthorsByNumber) {
        $script:ClassificationCache.prAuthorsByNumber = @{}
    }
    $cacheKey = "$Repo#$Number"
    if ($script:ClassificationCache.prAuthorsByNumber.ContainsKey($cacheKey)) {
        $cached = [string]$script:ClassificationCache.prAuthorsByNumber[$cacheKey]
        if ($cached) { return $cached }
    }
    $pr = Get-PullRequestState -Repo $Repo -Number $Number -Quiet
    $login = if ($pr) { [string](Get-ScalarValue $pr.author.login) } else { "" }
    if ($login) {
        $script:ClassificationCache.prAuthorsByNumber[$cacheKey] = $login
    }
    return $login
}

function Test-IsOwnPullRequestCredit([string]$Repo, [string]$Login, [int]$Number) {
    if (-not $Login -or $Number -le 0) { return $false }
    $authorLogin = Get-PullRequestAuthorLogin -Repo $Repo -Number $Number
    if (-not $authorLogin) { return $false }
    return ($authorLogin -eq $Login)
}

function Test-IsMaintainerReleaseVehiclePullRequest(
    [string]$Repo,
    [int]$Number,
    [string]$AuthorLogin,
    [string]$Title,
    [hashtable]$Exclusions
) {
    if (-not (Test-IsReleaseTitle -Text $Title)) { return $false }
    if (Test-IsLeaderboardExcludedLogin -Login $AuthorLogin -Exclusions $Exclusions) { return $true }
    if ($AuthorLogin -match '(?i)hermes$|nesquena') { return $true }
    return $false
}

function Test-IsMergedPullRequestState([string]$Repo, [int]$Number) {
    $pr = Get-PullRequestState -Repo $Repo -Number $Number -Quiet
    if (-not $pr) { return $false }
    return [string]$pr.state -eq "MERGED"
}

function Test-IsClosedNotMergedPullRequestState([string]$Repo, [int]$Number) {
    $pr = Get-PullRequestState -Repo $Repo -Number $Number -Quiet
    if (-not $pr) { return $false }
    return [string]$pr.state -eq "CLOSED"
}

function Test-HasPersistedPullRequestState([string]$Repo, [int]$Number) {
    $cacheKey = "$Repo#$Number"
    if ($script:PullRequestStateCache -and $script:PullRequestStateCache.ContainsKey($cacheKey)) {
        return $null -ne $script:PullRequestStateCache[$cacheKey]
    }
    if ($script:ClassificationCache.prPullStates -and $script:ClassificationCache.prPullStates.ContainsKey($cacheKey)) {
        return $true
    }
    return $false
}

function Test-IsVehiclePullRequest([string]$Repo, [int]$Number, [hashtable]$Exclusions) {
    $pr = Get-PullRequestState -Repo $Repo -Number $Number -Quiet
    if (-not $pr) { return $false }
    $login = Get-ScalarValue $pr.author.login
    return Test-IsMaintainerReleaseVehiclePullRequest -Repo $Repo -Number $Number -AuthorLogin $login -Title ([string]$pr.title) -Exclusions $Exclusions
}

function Confirm-UpstreamReleaseCreditMap(
    [string]$Repo,
    [hashtable]$ChangelogMap,
    [hashtable]$CommitMap,
    [hashtable]$MergedMap,
    [hashtable]$AbsorbedMap,
    [hashtable]$AbsorbCommitMap = @{},
    [hashtable]$ShipCommentMap = @{},
    [hashtable]$Exclusions
) {
    $verified = New-EmptyCreditMap
    $mergeChecks = 0
    $mergeFromCache = 0
    $sources = Merge-CreditMaps @($ChangelogMap, $CommitMap, $MergedMap, $AbsorbedMap, $AbsorbCommitMap, $ShipCommentMap)

    foreach ($login in $sources.Keys) {
        foreach ($num in @($sources[$login])) {
            if (-not (Test-IsOwnPullRequestCredit -Repo $Repo -Login $login -Number $num)) {
                continue
            }
            if (Test-IsVehiclePullRequest -Repo $Repo -Number $num -Exclusions $Exclusions) {
                continue
            }

            $fromMerged = $MergedMap.ContainsKey($login) -and $MergedMap[$login].Contains($num)
            $fromCommit = $CommitMap.ContainsKey($login) -and $CommitMap[$login].Contains($num)
            $fromAbsorbCommit = $AbsorbCommitMap.ContainsKey($login) -and $AbsorbCommitMap[$login].Contains($num)
            $fromShipComment = $ShipCommentMap.ContainsKey($login) -and $ShipCommentMap[$login].Contains($num)
            $fromAbsorbed = $AbsorbedMap.ContainsKey($login) -and $AbsorbedMap[$login].Contains($num)
            $fromChangelog = $ChangelogMap.ContainsKey($login) -and $ChangelogMap[$login].Contains($num)

            if ($fromMerged) {
                Add-CreditPair -Map $verified -Login $login -Number $num
                continue
            }
            if ($fromCommit) {
                if (Test-IsMergedPullRequestState -Repo $Repo -Number $num) {
                    Add-CreditPair -Map $verified -Login $login -Number $num
                    continue
                }
                $entry = Get-ExistingClosedClassificationEntry -Repo $Repo -Number $num
                if ($entry -and $entry.classification -eq "accepted-indirect") {
                    Add-CreditPair -Map $verified -Login $login -Number $num
                    continue
                }
                if ($entry -and $entry.classification -eq "shipped" -and $entry.viaUrl) {
                    Add-CreditPair -Map $verified -Login $login -Number $num
                }
                continue
            }
            if ($fromAbsorbCommit) {
                if (-not (Test-IsClosedNotMergedPullRequestState -Repo $Repo -Number $num)) { continue }
                if (Test-IsVehiclePullRequest -Repo $Repo -Number $num -Exclusions $Exclusions) { continue }
                Add-CreditPair -Map $verified -Login $login -Number $num
                continue
            }
            if ($fromShipComment) {
                Add-CreditPair -Map $verified -Login $login -Number $num
                continue
            }
            if ($fromAbsorbed) {
                if (Test-IsMergedPullRequestState -Repo $Repo -Number $num) { continue }
                if (-not (Test-IsClosedNotMergedPullRequestState -Repo $Repo -Number $num)) { continue }
                if (Test-IsVehiclePullRequest -Repo $Repo -Number $num -Exclusions $Exclusions) { continue }
                Add-CreditPair -Map $verified -Login $login -Number $num
                continue
            }
            if ($fromChangelog) {
                if (-not (Test-HasPersistedPullRequestState -Repo $Repo -Number $num)) {
                    $mergeChecks++
                } elseif (Test-IsMergedPullRequestState -Repo $Repo -Number $num) {
                    $mergeFromCache++
                }
                if (Test-IsMergedPullRequestState -Repo $Repo -Number $num) {
                    Add-CreditPair -Map $verified -Login $login -Number $num
                }
            }
        }
    }

    if ($mergeChecks -gt 0) {
        Write-ProgressHost "    Verified CHANGELOG merge state on $mergeChecks PR lookup(s)..." -ForegroundColor DarkGray
    } elseif ($mergeFromCache -gt 0) {
        Write-ProgressHost "    CHANGELOG merge checks from cache ($mergeFromCache PR(s))..." -ForegroundColor DarkGray
    }

    return $verified
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
    [int]$Top = 10,
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

    Write-ProgressHost "    Building release credit maps (CHANGELOG + merged + commits + absorbed)..." -ForegroundColor DarkGray
    $changelogMap = Get-WebuiChangelogCreditMap -Text $changelog.Text
    Write-ProgressHost "    Scanning contributor merged PRs..." -ForegroundColor DarkGray
    $mergedMap = Get-WebuiFilteredMergedPrCreditMap -Repo $Repo -Exclusions $Exclusions
    Write-ProgressHost "    Scanning master commits (shared scan, up to $script:WebuiAbsorbCommitScanMaxPages pages)..." -ForegroundColor DarkGray
    $commitScan = Get-WebuiMasterCommitScan -Repo $Repo
    Write-ProgressHost "    Extracting co-author credits from commits..." -ForegroundColor DarkGray
    $commitMap = Get-WebuiCommitCreditMap -Repo $Repo -CommitScan $commitScan
    Write-ProgressHost "    Resolving absorbed closed PR credits from release sections..." -ForegroundColor DarkGray
    $absorbedMap = Get-WebuiReleaseAbsorbedCreditMap -Text $changelog.Text -Repo $Repo -Exclusions $Exclusions
    Write-ProgressHost "    Scanning master commits for absorb-credits (source e)..." -ForegroundColor DarkGray
    $absorbCommitMap = Get-WebuiAbsorbCommitCreditMap -Repo $Repo -Exclusions $Exclusions -CommitScan $commitScan
    $preMerged = Merge-CreditMaps @($changelogMap, $mergedMap, $commitMap, $absorbedMap, $absorbCommitMap)
    Write-ProgressHost "    Classifying ship comments with priority-ladder (source d)..." -ForegroundColor DarkGray
    $scanLogins = @($preMerged.GetEnumerator() |
        Sort-Object { $_.Value.Count } -Descending |
        Select-Object -First $Top |
        ForEach-Object { $_.Key })
    Write-ProgressHost "    Ship-comment scan for top $($scanLogins.Count): $($scanLogins -join ', ')" -ForegroundColor DarkGray
    $shipCommentMap = Get-WebuiShipCommentCreditMap -Repo $Repo -Logins $scanLogins -Exclusions $Exclusions -CoAuthorIndex $commitScan.CoAuthorIndex -AlreadyCreditedMap $preMerged
    $authorVerifiedMap = Confirm-UpstreamReleaseCreditMap -Repo $Repo -ChangelogMap $changelogMap -CommitMap $commitMap -MergedMap $mergedMap -AbsorbedMap $absorbedMap -AbsorbCommitMap $absorbCommitMap -ShipCommentMap $shipCommentMap -Exclusions $Exclusions
    $counts = ConvertTo-CreditCountMap -CreditMap $authorVerifiedMap

    Write-ProgressHost "    Backfilling ranks $($Top + 1)+ from CONTRIBUTORS.md..." -ForegroundColor DarkGray
    $upstreamRanked = Get-ContributorsMdRankedCredits -Repo $Repo -Exclusions $Exclusions
    foreach ($entry in $upstreamRanked) {
        if (-not $counts.ContainsKey($entry.Login)) {
            $counts[$entry.Login] = $entry.Count
        }
    }

    Set-CachedReleaseCreditData -Repo $Repo -StartDate $StartDate -Counts $counts -Meta @{
        changelogSha = $changelog.Sha
        builtAt = (Get-Date).ToString("o")
        pairCount = ($authorVerifiedMap.Values | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum
    }

    return $counts
}

function Get-CommentBasedShippedCount([string]$Repo, [string]$Login, [hashtable]$CachedCommentCounts) {
    $raw = Invoke-Gh pr list --repo $Repo --author $Login --state closed --limit 500 --json "number" 2>$null
    if (-not $raw) { return $null }
    $closedPrs = @(($raw | ConvertFrom-Json) | ForEach-Object { $_ })
    $closedCount = $closedPrs.Count

    if ($CachedCommentCounts -and $CachedCommentCounts.ContainsKey($Login)) {
        $cached = $CachedCommentCounts[$Login]
        if ($cached.closedPrCount -eq $closedCount) {
            $mergedCount = Get-AuthorMergedPrCount -Repo $Repo -Login $Login
            if ($null -eq $mergedCount) { $mergedCount = 0 }
            $total = [int]$cached.shippedClosedCount + $mergedCount
            Write-Host "      $Login comment-shipped=$($cached.shippedClosedCount) merged=$mergedCount total=$total (cached)" -ForegroundColor DarkGray
            return $total
        }
    }

    $shippedCount = 0
    foreach ($pr in $closedPrs) {
        $comments = Invoke-Gh api "repos/$Repo/issues/$($pr.number)/comments" --jq '.[].body' 2>$null
        $allText = if ($comments) { $comments -join "`n" } else { "" }
        if ($allText -match '(?i)(shipped|cherry-picked|merged-via|salvaged\s+into)') { $shippedCount++ }
    }

    $mergedCount = Get-AuthorMergedPrCount -Repo $Repo -Login $Login
    if ($null -eq $mergedCount) { $mergedCount = 0 }
    $total = $shippedCount + $mergedCount

    if ($CachedCommentCounts) {
        $CachedCommentCounts[$Login] = @{ shippedClosedCount = $shippedCount; closedPrCount = $closedCount }
    }

    Write-Host "      $Login comment-shipped=$shippedCount merged=$mergedCount total=$total ($closedCount API calls)" -ForegroundColor DarkGray
    return $total
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
    [int]$MyRepoOpen,
    [hashtable]$CachedShippedCounts,
    [hashtable]$CachedCommentShippedCounts,
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
        $creditCounts = Get-ChangelogReleaseCreditCounts -Repo $Repo -StartDate $StartDate -Top $ClassifyTop -Exclusions $Exclusions -ForceRefresh:$ForceRefresh
        foreach ($login in $CommunityStats.Keys) {
            $count = if ($creditCounts.ContainsKey($login)) { [int]$creditCounts[$login] } else { 0 }
            $shippedCountsToSave[$login] = $count
            $CommunityStats[$login].credited = $count
            $CommunityStats[$login].estimated = $false
            $CommunityStats[$login].shippedClassified = $true
        }

        # Author's per-PR classification is the most accurate source for our own count
        if ($Author -and $CommunityStats.ContainsKey($Author) -and $MyRepoCredited -gt $CommunityStats[$Author].credited) {
            $shippedCountsToSave[$Author] = $MyRepoCredited
            $CommunityStats[$Author].credited = $MyRepoCredited
            $CommunityStats[$Author].open = $MyRepoOpen
            $CommunityStats[$Author].total = $MyRepoCredited + $MyRepoOpen
        }

        return @{
            ShippedCountsToSave = $shippedCountsToSave
            CommentShippedCounts = @{}
            ClassifiedFromCache = 0
            ClassifiedLive = $CommunityStats.Count
        }
    }

    $classifyLogins = @(Get-TopCreditedLogins -Stats $CommunityStats -Top $ClassifyTop)
    foreach ($login in $CommunityStats.Keys) {
        if ($login -eq $Author) {
            # Always use live counts for the author regardless of cache state
            $count = $MyRepoCredited
            $CommunityStats[$login].credited = $count
            $CommunityStats[$login].open = $MyRepoOpen
            $CommunityStats[$login].total = $count + $MyRepoOpen
            $CommunityStats[$login].estimated = $false
            $CommunityStats[$login].shippedClassified = $true
        } elseif ($UseCachedShipped -and -not $ForceRefresh -and $CachedShippedCounts.ContainsKey($login)) {
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
            $count = Get-ContributorShippedCount -Repo $Repo -Login $login
            $classifiedLive++
            Write-Host "      $login shipped=$count (classified)" -ForegroundColor DarkGray
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
