param(
    [string]$Author = "rodboev",
    [string[]]$Repos = @("nesquena/hermes-webui"
        , "kenn-io/agentsview"
        , "thedotmack/claude-mem"
        , "headroomlabs-ai/headroom"
        , "mem0ai/mem0"
        , "stablyai/orca"
        # , "NVIDIA/SkillSpector"
        # , "NousResearch/hermes-agent"
        # , "cline/cline",
        # , "continuedev/continue"
        # , "CopilotKit/CopilotKit"
        # , "MemPalace/mempalace"
        # , "mastra-ai/mastra"
        # , "github/github-mcp-server"
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
    [int]$LeaderboardRefreshTtlHours = 24,
    [int]$LeaderboardTop = 10,
    [switch]$RebuildCache,
    [switch]$RebuildClassifications,
    [switch]$RefreshLeaderboardCache,
    [switch]$VerifyWebuiCreditsOnly,
    [switch]$ForceWrite
)

trap {
    Write-Host "`nFatal error: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        Read-Host "`nPress Enter to close"
    }
    break
}

$script:GenerateStartedAt = Get-Date

$shippedPatterns = @("shipped", "cherry-picked", "merged-via", "salvaged into")
$duplicatePatterns = @("duplicate")
$supersededPatterns = @("supersede", "consolidat", "closing in favor", "closed in favor")
# Maintainer phrasing that preserves the author's credit on shipped work. When
# this co-occurs with shipping evidence (a release tag or a shipping verb), a
# "superseded"/"integrated" close is actually a credited landing, not a loss.
$creditPatterns = @("co-author", "coauthor", "co-authored", "authorship", "attribution", "credited")
$continuationPatterns = @("same credit", "same commit", "same change", "reopen")
$withdrawnPattern = '(?i)\bwithdraw(?:ing|n)?\b'
$authorClosePattern = '(?i)\bclos(?:ing|ed|e)\b'
$mergedCarryForwardPattern = '(?i)\bmerge(?:d|s|ing)?(?:\s+to\s+main)?\b'
$DefaultLeaderboardVisible = 10
$DefaultPrTableVisible = 20
$LeaderboardMax = 50
$LeaderboardClassifyTop = $LeaderboardTop
$LeaderboardRateWindowDays = 7
$MinSpeculativeReferencedPrNumber = 100
$LeaderboardCacheKeyVersion = "community-shipped-v4"
$ClassificationCacheVersion = 3
$DefaultReportStartDate = [datetime]"2026-06-02"
$GhInvokeTimeoutSeconds = 120
$GhInvokeSlowLogSeconds = 5
$GhRetryBaseDelaySeconds = 5
$GhRetryMaxDelaySeconds = 300

$RepoLeaderboardConfig = @{
    "nesquena/hermes-webui" = @{
        MaintainerLogins = @("nesquena")
        IntegrationBots = @("nesquena-hermes")
    }
    "kenn-io/agentsview" = @{
        MaintainerLogins = @("wesm", "mariusvniekerk", "cpcloud")
        IntegrationBots = @()
    }
    "thedotmack/claude-mem" = @{
        MaintainerLogins = @("thedotmack")
        IntegrationBots = @()
    }
    "headroomlabs-ai/headroom" = @{
        MaintainerLogins = @("chopratejas", "DevanshiVyas", "JerrettDavis")
        IntegrationBots = @()
    }
    "mem0ai/mem0" = @{
        MaintainerLogins = @("taranjeet", "deshraj", "kartik-mem0", "chaithanyak42", "prathameshagrawal", "agumpandey")
        IntegrationBots = @()
    }
    "stablyai/orca" = @{
        MaintainerLogins = @("nwparker", "AmethystLiang", "Jinwoo-H", "brennanb2025", "tmchow")
        IntegrationBots = @("buf0-bot[bot]")
    }
    "cline/cline" = @{
        MaintainerLogins = @("abeatrix", "BarreiroT", "celestial-vault", "robinnewhouse", "Garoth", "arafatkatze", "dominiccooney", "NightTrek", "tseglevskiy", "JicLotus", "mkondratek")
        IntegrationBots = @()
    }
    "continuedev/continue" = @{
        MaintainerLogins = @("Patrick-Erichsen", "TyDunn", "tomasz-stefaniak", "BekahHW", "sestinj")
        IntegrationBots = @("continue-agent")
    }
    "CopilotKit/CopilotKit" = @{
        MaintainerLogins = @("CopilotKit", "ataibarkai", "samjulien", "maxkorp", "NathanTarbert", "johnprg", "Anmol-Baranwal")
        IntegrationBots = @()
    }
    "MemPalace/mempalace" = @{
        MaintainerLogins = @("igorls", "jphein", "milla-jovovich")
        IntegrationBots = @()
    }
    "mastra-ai/mastra" = @{
        MaintainerLogins = @("abhiaiyer91", "TylerBarnes", "LekoArts", "NikAiyer", "smthomas", "YujohnNattrass", "intojhanurag", "CalebBarnes", "graysonhicks", "mikhael28", "taofeeq-deru", "jsumnersmith")
        IntegrationBots = @("devin-ai-integration")
    }
    "github/github-mcp-server" = @{
        MaintainerLogins = @("toby", "alondahari", "koesie10", "owenniblock", "rubiojr", "JoannaaKL", "juruen", "mntlty", "SamMorrowDrums", "williammartin", "tommaso-moro", "MattBabbage", "gokhanarkan", "stephenotalora", "tmelliottjr", "omgitsads", "GeekTrainer", "chiedo", "jshorty", "reneexeener", "timrogers")
        IntegrationBots = @()
    }
    "lsdefine/GenericAgent" = @{
        MaintainerLogins = @("lsdefine")
        IntegrationBots = @()
    }
}

# (maxAgeDays, ttlDays) pairs; $null maxAge = catch-all
$ClassificationTtlProfiles = @{
    stable   = @(@(30, 30), @(120, 90), @($null, 180))
    timeline = @(@(14, 14), @(60, 30), @($null, 90))
    volatile = @(@(14, 7), @(60, 30), @($null, 90))
}
$ClassificationTtlProfileFor = @{
    "shipped/direct-merge" = "stable"
    "shipped/timeline" = "timeline"
    "shipped/*" = "volatile"
    "accepted-indirect/*" = "volatile"
    "lost/*" = "stable"
    "withdrawn/*" = "stable"
}

$ClassificationMeta = [ordered]@{
    shipped             = @{ Label = "Shipped"; Tag = "tag-shipped"; Color = "Green"; Desc = "Verified via merged release PR, maintainer release evidence, or indirect accepted sibling" }
    "accepted-indirect" = @{ Label = "Shipped"; Tag = "tag-shipped"; Color = "Cyan" }
    open                = @{ Label = "Open"; Tag = "tag-open"; Color = "Blue"; Desc = "Pending review" }
    withdrawn           = @{ Label = "Withdrawn"; Tag = "tag-withdrawn"; Color = "DarkGray"; Desc = "Closed without maintainer action" }
    superseded          = @{ Label = "Superseded"; Tag = "tag-superseded"; Color = "DarkYellow"; Desc = "Replaced by a newer PR" }
    lost                = @{ Label = "Lost"; Tag = "tag-lost"; Color = "Red"; Desc = "Closed without acceptance" }
}

$EasternTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
$script:RepoOwnerCache = @{}
$script:PullRequestStateCache = @{}
$script:PullRequestCommitAuthorCache = @{}
$script:PullRequestEvidenceCache = @{}
$script:ClassificationCache = @{
    version = $ClassificationCacheVersion
    entries = @{}
    leaderboards = @{}
    contributorsMdSeeds = @{}
    prAuthorsByNumber = @{}
}
$script:ClassificationCacheHits = 0
$script:LeaderboardCacheHits = 0

function Set-ProcessEnvironmentFromCurrent([System.Diagnostics.ProcessStartInfo]$ProcessInfo) {
    foreach ($entry in [Environment]::GetEnvironmentVariables([EnvironmentVariableTarget]::Process).GetEnumerator()) {
        $ProcessInfo.Environment[$entry.Key] = [string]$entry.Value
    }
    $ProcessInfo.Environment["GIT_TERMINAL_PROMPT"] = "0"
    $ProcessInfo.Environment["GH_PROMPT_DISABLED"] = "1"
    $ProcessInfo.Environment["GH_NO_UPDATE_NOTIFIER"] = "1"
    $ProcessInfo.Environment["GCM_INTERACTIVE"] = "never"
}

function Test-IsWindowsRuntime {
    if (Get-Variable -Name IsWindows -Scope Global -ErrorAction SilentlyContinue) {
        return [bool]$global:IsWindows
    }
    return [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )
}

function Resolve-GhExecutable {
    $command = Get-Command gh.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command gh -CommandType Application -ErrorAction SilentlyContinue
    }
    if ($command) { return $command.Source }
    return "gh"
}

function Get-GhCommandDisplayText([string]$ArgText, [int]$MaxLength = 160) {
    if ($ArgText.Length -le $MaxLength) {
        return $ArgText
    }
    return $ArgText.Substring(0, $MaxLength) + "..."
}

function Get-GhRetryReason {
    param(
        [string]$StdErr,
        [switch]$TimedOut
    )

    if ($TimedOut) {
        return "gh timeout"
    }

    if ([string]::IsNullOrWhiteSpace($StdErr)) {
        return $null
    }

    $message = $StdErr.Trim()
    if ($message -match '(?i)secondary rate limit|rate limit exceeded|api rate limit|abuse detection') {
        return "GitHub rate limit"
    }
    if ($message -match '(?i)HTTP 5\d\d|status code 5\d\d|bad gateway|service unavailable|gateway timeout') {
        return "GitHub server error"
    }
    if ($message -match '(?i)timed out|timeout|context deadline exceeded|i/o timeout|tls handshake timeout|connection.*(reset|closed|refused|aborted)|unexpected eof|request canceled|temporary failure|temporarily unavailable') {
        return "network error"
    }
    return $null
}

function Get-GhRetryDelaySeconds([int]$Attempt, [string]$Reason) {
    $delay = [math]::Min($GhRetryMaxDelaySeconds, $GhRetryBaseDelaySeconds * [math]::Pow(2, [math]::Min($Attempt - 1, 6)))
    if ($Reason -eq "GitHub rate limit") {
        $delay = [math]::Max(60, $delay)
    }
    return [int][math]::Ceiling($delay)
}

function Invoke-Gh {
    param(
        [switch]$SuppressErrors,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GhArgs
    )

    if (-not (Test-IsWindowsRuntime)) {
        $env:GIT_TERMINAL_PROMPT = "0"
        $env:GH_PROMPT_DISABLED = "1"
        $env:GH_NO_UPDATE_NOTIFIER = "1"
        $env:GCM_INTERACTIVE = "never"
        if ($SuppressErrors) {
            & gh @GhArgs 2>$null
        } else {
            & gh @GhArgs
        }
        return
    }

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

    $commandLabel = "gh $(Get-GhCommandDisplayText -ArgText ($normalizedArgs -join ' '))"
    Write-ProgressHost "  > $commandLabel" -ForegroundColor DarkGray
    $ghExe = Resolve-GhExecutable
    $attempt = 0
    while ($true) {
        $attempt++

        $psi = [System.Diagnostics.ProcessStartInfo]::new($ghExe)
        foreach ($arg in $normalizedArgs) {
            [void]$psi.ArgumentList.Add($arg)
        }
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        Set-ProcessEnvironmentFromCurrent -ProcessInfo $psi

        $p = [System.Diagnostics.Process]::Start($psi)
        # Read streams before WaitForExit to prevent deadlock when output exceeds the OS pipe buffer.
        $stdoutTask = $p.StandardOutput.ReadToEndAsync()
        $stderrTask = $p.StandardError.ReadToEndAsync()
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $timedOut = -not $p.WaitForExit($GhInvokeTimeoutSeconds * 1000)
        $sw.Stop()

        if ($timedOut) {
            try {
                if (-not $p.HasExited) {
                    $p.Kill($true)
                }
            } catch {}
            try {
                $p.WaitForExit()
            } catch {}
            $global:LASTEXITCODE = 124
        } else {
            $global:LASTEXITCODE = $p.ExitCode
        }

        if ($sw.Elapsed.TotalSeconds -ge $GhInvokeSlowLogSeconds) {
            Write-ProgressHost "  ^ slow ($([math]::Round($sw.Elapsed.TotalSeconds, 1))s)" -ForegroundColor Yellow
        }

        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $retryReason = if ($global:LASTEXITCODE -ne 0) {
            Get-GhRetryReason -StdErr $stderr -TimedOut:$timedOut
        } else {
            $null
        }

        if ($global:LASTEXITCODE -eq 0) {
            if ($stderr -and -not $SuppressErrors) {
                [Console]::Error.Write($stderr)
            }
            return $stdout.TrimEnd()
        }

        if ($retryReason) {
            $delaySeconds = Get-GhRetryDelaySeconds -Attempt $attempt -Reason $retryReason
            $detail = ""
            if (-not [string]::IsNullOrWhiteSpace($stderr)) {
                $detail = Get-GhCommandDisplayText -ArgText (($stderr -replace '\s+', ' ').Trim()) -MaxLength 140
            }
            Write-ProgressHost "  WARN: $retryReason for $commandLabel; retry $attempt in ${delaySeconds}s" -ForegroundColor Yellow
            if ($detail) {
                Write-ProgressHost "    $detail" -ForegroundColor DarkYellow
            }
            Start-Sleep -Seconds $delaySeconds
            continue
        }

        if ($timedOut) {
            Write-ProgressHost "  gh timed out after ${GhInvokeTimeoutSeconds}s: $commandLabel" -ForegroundColor Red
        }
        if ($stderr -and -not $SuppressErrors) {
            [Console]::Error.Write($stderr)
        }
        return $stdout.TrimEnd()
    }
}

function Read-Utf8TextFile([string]$Path) {
    $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    return [System.IO.File]::ReadAllText($Path, $utf8)
}

function Write-ProgressHost {
    param(
        [string]$Message,
        [ConsoleColor]$ForegroundColor = [ConsoleColor]::DarkGray,
        [switch]$NoNewline
    )

    Write-Host $Message -ForegroundColor $ForegroundColor -NoNewline:$NoNewline
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

function Get-CollapseCaretHtml([bool]$Up) {
    if ($Up) { "&#9650;" } else { "&#9660;" }
}

function Get-ExpandRowHtml([string]$BlockId, [string]$Label, [int]$Colspan = 6) {
    $caret = Get-CollapseCaretHtml $false
    "  <tr class=`"expand-row`" onclick=`"toggleCollapsedTable('$BlockId', event)`"><td colspan=`"$Colspan`">$Label <span class=`"caret`">$caret</span></td></tr>`n"
}

function Get-CollapseOverlayHtml([string]$BlockId, [string]$Label = "Collapse") {
    $caret = Get-CollapseCaretHtml $true
    "<div class=`"overlay-row`" onclick=`"toggleCollapsedTable('$BlockId', event)`">$Label <span class=`"caret`">$caret</span></div>`n"
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

function Test-MatchesAnyPattern([string]$Text, [string[]]$Patterns) {
    foreach ($p in $Patterns) {
        if ($Text -match [regex]::Escape($p)) { return $true }
    }
    return $false
}

function Get-RepoLabel([string]$Repo) {
    $repoShort = ($Repo -split '/')[-1]
    switch ($repoShort) {
        "hermes-webui" { return "webui" }
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

function New-LeaderboardStat(
    [int]$Total,
    [int]$Open,
    [int]$RecentCount,
    [string]$LastCreatedAt,
    [datetime]$Now,
    [double]$RateWindowDays
) {
    $last = $null
    if ($LastCreatedAt) {
        try { $last = [datetime]$LastCreatedAt } catch {}
    }
    return @{
        credited = [math]::Max(0, $Total - $Open)
        open = $Open
        total = $Total
        recentCount = $RecentCount
        rate = if ($RateWindowDays -gt 0) { [math]::Round($RecentCount / $RateWindowDays, 1) } else { 0 }
        idle = if ($last) { [math]::Round(($Now - $last).TotalDays, 1) } else { 999 }
        lastCreatedAt = if ($LastCreatedAt) { $LastCreatedAt } else { "" }
    }
}

function ConvertTo-StoredLeaderboardStat([object]$Stat) {
    return @{
        total = [int]$Stat.total
        open = [int]$Stat.open
        recentCount = [int]$Stat.recentCount
        lastCreatedAt = if ($Stat.lastCreatedAt) { [string]$Stat.lastCreatedAt } else { "" }
    }
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

function Get-ReposByShippedCount {
    param(
        [string[]]$Repos,
        [object[]]$PullRequests
    )

    return @($Repos | Sort-Object -Property @{
        Expression = {
            $repo = $_
            @($PullRequests | Where-Object {
                $_.repo -eq $repo -and $_.classification -in @("shipped", "accepted-indirect")
            }).Count
        }
        Descending = $true
    }, { $_ })
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

function Get-PullRequestState([string]$Repo, [int]$Number, [switch]$Quiet, [switch]$RequireBody) {
    $cacheKey = "$Repo#$Number"
    if ($script:PullRequestStateCache.ContainsKey($cacheKey)) {
        $cached = $script:PullRequestStateCache[$cacheKey]
        $cachedBody = if ($cached) { [string](Get-ScalarValue $cached.body) } else { "" }
        if (-not $RequireBody -or $cachedBody) {
            return $cached
        }
    }
    if (-not $script:ClassificationCache) {
        $script:ClassificationCache = @{}
    }
    if (-not $script:ClassificationCache["prPullStates"]) {
        $script:ClassificationCache["prPullStates"] = @{}
    }
    if ($script:ClassificationCache["prPullStates"].ContainsKey($cacheKey)) {
        $stored = $script:ClassificationCache["prPullStates"][$cacheKey]
        if ($stored.state -eq "NOT_FOUND") {
            $script:PullRequestStateCache[$cacheKey] = $null
            return $null
        }
        $storedBody = [string](Get-ScalarValue $stored.body)
        if (-not $RequireBody -or $storedBody) {
            $result = [pscustomobject]@{
                number = $Number
                state = [string]$stored.state
                mergedAt = if ($stored.mergedAt) { $stored.mergedAt } else { $null }
                title = [string]$stored.title
                url = "https://github.com/$Repo/pull/$Number"
                author = @{ login = [string]$stored.author }
                body = $storedBody
            }
            $script:PullRequestStateCache[$cacheKey] = $result
            return $result
        }
    }
    $ghParams = @{
        SuppressErrors = [bool]$Quiet
        GhArgs = @('pr', 'view', "$Number", '--repo', $Repo, '--json', 'number,state,mergedAt,title,url,author,body')
    }
    $raw = $null
    try {
        $raw = Invoke-Gh @ghParams 2>$null
    } catch {}
    $result = $null
    if ($raw) {
        try { $result = $raw | ConvertFrom-Json } catch {}
    }
    $script:PullRequestStateCache[$cacheKey] = $result
    if ($result) {
        $authorLogin = ""
        if ($result.author -and $result.author.login) {
            $authorLogin = [string](Get-ScalarValue $result.author.login)
        }
        $script:ClassificationCache["prPullStates"][$cacheKey] = @{
            state = [string]$result.state
            mergedAt = if ($result.mergedAt) { [string]$result.mergedAt } else { "" }
            title = [string]$result.title
            author = $authorLogin
            body = [string](Get-ScalarValue $result.body)
        }
        if ($authorLogin) {
            if (-not $script:ClassificationCache["prAuthorsByNumber"]) {
                $script:ClassificationCache["prAuthorsByNumber"] = @{}
            }
            $script:ClassificationCache["prAuthorsByNumber"][$cacheKey] = $authorLogin
        }
    } else {
        $script:ClassificationCache["prPullStates"][$cacheKey] = @{ state = "NOT_FOUND" }
    }
    return $result
}

function Get-PullRequestCommitAuthorLogins([string]$Repo, [int]$Number, [switch]$Quiet) {
    $cacheKey = "$Repo#$Number"
    if ($script:PullRequestCommitAuthorCache.ContainsKey($cacheKey)) {
        return $script:PullRequestCommitAuthorCache[$cacheKey]
    }

    $logins = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $ghParams = @{
        SuppressErrors = [bool]$Quiet
        GhArgs = @('pr', 'view', "$Number", '--repo', $Repo, '--json', 'commits')
    }
    $raw = $null
    try {
        $raw = Invoke-Gh @ghParams 2>$null
    } catch {}
    $result = $null
    if ($raw) {
        try { $result = $raw | ConvertFrom-Json } catch {}
    }

    if ($result -and $result.commits) {
        foreach ($commit in @($result.commits)) {
            foreach ($author in @($commit.authors)) {
                $login = [string](Get-ScalarValue $author.login)
                if ($login) {
                    $null = $logins.Add($login)
                }
            }
        }
    }

    $script:PullRequestCommitAuthorCache[$cacheKey] = $logins
    return $logins
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

function Test-IsMaintainerComment([string]$Repo, [object]$Comment) {
    $login = Get-ScalarValue $Comment.author.login
    if ($login) {
        $config = $RepoLeaderboardConfig[$Repo]
        if ($config) {
            if ($config.MaintainerLogins -and $login -in @($config.MaintainerLogins)) { return $true }
            if ($config.IntegrationBots -and $login -in @($config.IntegrationBots)) { return $true }
        }
    }

    return $Comment.authorAssociation -and $Comment.authorAssociation -in @("OWNER", "COLLABORATOR", "MEMBER")
}

function Test-HasMaintainerNonBotComment([object]$PullRequest, [object]$Evidence) {
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($comment.author.login -eq "greptile-apps") { continue }
        if ($authorLogin -and $comment.author.login -eq $authorLogin) { continue }
        if (Test-IsMaintainerComment -Repo $PullRequest.repo -Comment $comment) {
            return $true
        }
        $body = [string]$comment.body
        if (Test-MatchesAnyPattern -Text $body -Patterns $shippedPatterns) { return $true }
        if ([bool](Get-ReleaseTag $body)) { return $true }
        if (Test-MatchesAnyPattern -Text $body -Patterns $creditPatterns) { return $true }
    }

    return $false
}

function Test-IsAuthorWithdrawnEvidence([object]$PullRequest, [object]$Evidence) {
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }
    if (-not $authorLogin) { return $false }

    $authorCommented = $false
    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($comment.author.login -ne $authorLogin) { continue }
        $authorCommented = $true
        $body = [string]$comment.body
        if ($body -match $withdrawnPattern) { return $true }
        if (Test-MatchesAnyPattern -Text $body -Patterns $supersededPatterns) { return $true }
        if ($body -match $authorClosePattern) { return $true }
    }

    if ($authorCommented -and -not (Test-HasMaintainerNonBotComment -PullRequest $PullRequest -Evidence $Evidence)) {
        return $true
    }

    return $false
}

function Test-IsSupersededEvidence([object]$PullRequest, [object]$Evidence) {
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($authorLogin -and $comment.author.login -eq $authorLogin) { continue }
        if (-not (Test-IsMaintainerComment -Repo $PullRequest.repo -Comment $comment)) { continue }
        if (Test-MatchesAnyPattern -Text ([string]$comment.body) -Patterns $supersededPatterns) {
            if (-not (Test-MatchesAnyPattern -Text ([string]$comment.body) -Patterns $continuationPatterns)) {
                return $true
            }
        }
    }

    return $false
}

function Test-HasSupersededReference([object]$PullRequest, [object]$Evidence) {
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($authorLogin -and $comment.author.login -eq $authorLogin) { continue }
        if (Test-MatchesAnyPattern -Text ([string]$comment.body) -Patterns $supersededPatterns) {
            if (-not (Test-MatchesAnyPattern -Text ([string]$comment.body) -Patterns $continuationPatterns)) {
                return $true
            }
        }
    }
    return $false
}

function Get-CreditedShipEvidence([object]$PullRequest, [object]$Evidence) {
    # A maintainer comment that both shows the work shipped (a release tag or a
    # shipping verb) and preserves the author's credit (co-author trailer,
    # authorship/attribution preserved, "credited"). This is a shipped landing,
    # not a superseding loss, even when the maintainer also writes "superseded"
    # or "closing as integrated". Returns the integration PR number when one can
    # be parsed from the comment, otherwise an entry with ViaNumber = 0.
    $authorLogin = Get-ScalarValue $PullRequest.author.login
    if (-not $authorLogin) { $authorLogin = $Author }

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($authorLogin -and $comment.author.login -eq $authorLogin) { continue }
        if (-not (Test-IsMaintainerComment -Repo $PullRequest.repo -Comment $comment)) { continue }
        $body = [string]$comment.body
        if (-not $body) { continue }
        $shipped = [bool](Get-ReleaseTag $body) -or (Test-MatchesAnyPattern -Text $body -Patterns $shippedPatterns)
        if (-not $shipped) { continue }
        if (-not (Test-MatchesAnyPattern -Text $body -Patterns $creditPatterns)) { continue }
        $viaMatch = [regex]::Match($body, '(?i)\b(?:via|by|into|in)\s+#(\d+)')
        $viaNumber = if ($viaMatch.Success) { [int]$viaMatch.Groups[1].Value } else { 0 }
        return @{ ViaNumber = $viaNumber }
    }

    return $null
}

function Get-PullRequestReferenceText([string]$Repo, [int]$Number) {
    $details = Get-PullRequestState -Repo $Repo -Number $Number -Quiet -RequireBody
    if (-not $details) { return "" }

    $evidence = Get-PullRequestEvidence -Repo $Repo -Number $Number
    return @(
        (Get-ScalarValue $details.title),
        (Get-ScalarValue $details.body),
        (Get-NonBotCommentText -Evidence $evidence)
    ) -join "`n---`n"
}

function Get-PullRequestReferenceContexts(
    [string]$Text,
    [string]$Repo,
    [int]$Number,
    [int]$Radius = 80
) {
    if (-not $Text) { return @() }

    $repoEscaped = [regex]::Escape($Repo)
    $pattern = "(?i)(https://github\.com/$repoEscaped/pull/$Number\b|(?<![\w/])#$Number\b)"
    $contexts = @()
    foreach ($line in ($Text -split "\r?\n")) {
        if ($line -match $pattern) {
            $contexts += $line
        }
    }
    foreach ($match in [regex]::Matches($Text, $pattern)) {
        $start = [Math]::Max(0, $match.Index - $Radius)
        $length = [Math]::Min($Text.Length - $start, $match.Length + (2 * $Radius))
        $contexts += $Text.Substring($start, $length)
    }
    return @($contexts | Select-Object -Unique)
}

function Test-HasPositivePullRequestReferenceContext(
    [string]$Text,
    [string]$Repo,
    [int]$Number,
    [string]$AuthorLogin
) {
    $negativePattern = '(?i)\b(?:supersed(?:e|ed|es|ing)|alternative|chosen\s+over|rather\s+than|instead\s+of|in\s+favor\s+of|browser-reserved|moot)\b'
    $positivePattern = '(?i)\b(?:ship(?:s|ped|ping)?|release(?:d)?|credit(?:ed)?|co-auth(?:or|ored)|authorship|attribution|carry(?:ing|ied)\s+forward|land(?:ed|ing)|merge(?:d|s|ing)?|fix(?:es|ed)?|close(?:s|d)?)\b'
    $authorPattern = if ($AuthorLogin) { "(?i)(?:^|[^\w])@$([regex]::Escape($AuthorLogin))(?:[^\w]|$)" } else { "" }

    foreach ($context in @(Get-PullRequestReferenceContexts -Text $Text -Repo $Repo -Number $Number)) {
        if ($context -match $negativePattern) { continue }
        if ($context -match $positivePattern) { return $true }
        if ($authorPattern -and $context -match $authorPattern) { return $true }
    }

    return $false
}

function Test-IsPositiveReleaseReferenceToPullRequest(
    [string]$Repo,
    [object]$OriginalPr,
    [object]$ReleasePr
) {
    if (-not $ReleasePr) { return $false }
    $referenceText = Get-PullRequestReferenceText -Repo $Repo -Number ([int]$ReleasePr.number)
    if (-not $referenceText) { return $false }

    $authorLogin = [string](Get-ScalarValue $OriginalPr.author.login)
    return Test-HasPositivePullRequestReferenceContext -Text $referenceText -Repo $Repo -Number ([int]$OriginalPr.number) -AuthorLogin $authorLogin
}

function Test-HasMaintainerCreditedReferenceToPullRequest(
    [string]$Repo,
    [object]$OriginalPr,
    [object]$Evidence,
    [int]$MergedPrNumber
) {
    if (-not $Evidence) { return $false }

    $authorLogin = Get-ScalarValue $OriginalPr.author.login
    if (-not $authorLogin) { $authorLogin = $Author }

    foreach ($comment in @($Evidence.comments.nodes)) {
        if ($authorLogin -and $comment.author.login -eq $authorLogin) { continue }
        if (-not (Test-IsMaintainerComment -Repo $Repo -Comment $comment)) { continue }

        $body = [string]$comment.body
        if (-not $body) { continue }
        if (-not ($body -match "(?i)(?:^|[^\w])#$MergedPrNumber(?:[^\w]|$)" -or (Test-IsExplicitPullRequestReference -Text $body -Number $MergedPrNumber))) { continue }
        if (-not (Test-MatchesAnyPattern -Text $body -Patterns $creditPatterns)) { continue }
        if (
            (Test-MatchesAnyPattern -Text $body -Patterns $supersededPatterns) -or
            (Test-MatchesAnyPattern -Text $body -Patterns $continuationPatterns) -or
            ($body -match $mergedCarryForwardPattern)
        ) {
            return $true
        }
    }

    return $false
}

function Test-IsCreditedMergedSiblingByMaintainerCarryForward(
    [string]$Repo,
    [object]$OriginalPr,
    [object]$Evidence,
    [object]$MergedPr
) {
    if (-not $MergedPr) { return $false }
    if (-not (Test-HasMaintainerCreditedReferenceToPullRequest -Repo $Repo -OriginalPr $OriginalPr -Evidence $Evidence -MergedPrNumber ([int]$MergedPr.number))) {
        return $false
    }

    $originalLogin = [string](Get-ScalarValue $OriginalPr.author.login)
    if (-not $originalLogin) { return $false }

    $mergedAuthorLogins = Get-PullRequestCommitAuthorLogins -Repo $Repo -Number ([int]$MergedPr.number) -Quiet
    return $mergedAuthorLogins.Contains($originalLogin)
}

function Test-IsCreditedMergedSibling([string]$Repo, [object]$OriginalPr, [object]$MergedPr, [object]$Evidence) {
    if (-not $MergedPr) { return $false }

    $originalNumber = [int]$OriginalPr.number
    $referenceText = Get-PullRequestReferenceText -Repo $Repo -Number $MergedPr.number
    if ($referenceText) {
        $originalLogin = [string](Get-ScalarValue $OriginalPr.author.login)
        if (Test-HasPositivePullRequestReferenceContext -Text $referenceText -Repo $Repo -Number $originalNumber -AuthorLogin $originalLogin) {
            return $true
        }
    }

    return Test-IsCreditedMergedSiblingByMaintainerCarryForward -Repo $Repo -OriginalPr $OriginalPr -Evidence $Evidence -MergedPr $MergedPr
}

function Get-ReferencedMergedPullRequest([string]$Repo, [object]$OriginalPr, [object]$Evidence, [string]$Text) {
    if (-not $Text) { return $null }
    $matches = [regex]::Matches($Text, '#(\d+)')
    $seen = @{}
    foreach ($match in $matches) {
        $num = [int]$match.Groups[1].Value
        if ($seen.ContainsKey($num)) { continue }
        $seen[$num] = $true
        if (-not (Test-ShouldResolveReferencedPullRequest -Text $Text -Number $num)) { continue }
        $pr = Get-PullRequestState -Repo $Repo -Number $num -Quiet
        if ($pr -and ($pr.state -eq "MERGED" -or $pr.mergedAt) -and (Test-IsCreditedMergedSibling -Repo $Repo -OriginalPr $OriginalPr -MergedPr $pr -Evidence $Evidence)) {
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

        $mergedPrNumber = [int](Get-ScalarValue $node.source.number)
        if ($mergedPrNumber -le 0) { continue }
        $pr = Get-PullRequestState -Repo $Repo -Number $mergedPrNumber -Quiet
        if ($pr -and (Test-IsCreditedMergedSibling -Repo $Repo -OriginalPr $OriginalPr -MergedPr $pr -Evidence $Evidence)) {
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

    $recentCount = 0
    foreach ($pr in $prs) {
        if ($pr.createdAt) {
            try {
                if ([datetime]$pr.createdAt -gt $RateSinceDate) { $recentCount++ }
            } catch {}
        }
    }

    $last = if ($dates.Count -gt 0) { $dates[-1] } else { $null }
    return New-LeaderboardStat -Total $prs.Count -Open $openCount -RecentCount $recentCount `
        -LastCreatedAt $(if ($last) { $last.ToString("o") } else { "" }) -Now $Now -RateWindowDays $RateWindowDays
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
            $q = ($queryLines -join "`n")
            $result = Invoke-Gh api graphql -f "query=$q" 2>$null | ConvertFrom-Json
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
                    $stats[$login] = New-LeaderboardStat -Total $totalCount -Open $openCount -RecentCount $recentCount `
                        -LastCreatedAt $(if ($last) { $last.ToString("o") } else { "" }) -Now $Now -RateWindowDays $RateWindowDays
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
        contributorsMdSeeds = @{}
        prAuthorsByNumber = @{}
        prPullStates = @{}
        commitCreditMap = @{}
        absorbCommitMap = @{}
        shipCommentClassifications = @{}
        mergedPrCreditMap = @{}
        absorbedCreditMap = @{}
        commitScanMeta = @{}
    }
}

function Import-ClassificationCache([string]$Path, [switch]$ForceRebuild, [switch]$ForceRebuildClassifications) {
    if ($ForceRebuild -or -not (Test-Path -LiteralPath $Path)) {
        return New-ClassificationCache
    }

    try {
        $raw = Read-Utf8TextFile -Path $Path | ConvertFrom-Json
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
                    $stats[$statProp.Name] = ConvertTo-StoredLeaderboardStat $statProp.Value
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

            $releaseCreditCounts = @{}
            if ($repoProp.Value.releaseCreditCounts) {
                foreach ($creditProp in $repoProp.Value.releaseCreditCounts.PSObject.Properties) {
                    $releaseCreditCounts[$creditProp.Name] = [int]$creditProp.Value
                }
            }

            $releaseCreditMeta = $null
            if ($repoProp.Value.releaseCreditMeta) {
                $releaseCreditMeta = @{
                    changelogSha = $repoProp.Value.releaseCreditMeta.changelogSha
                    builtAt = $repoProp.Value.releaseCreditMeta.builtAt
                    pairCount = $repoProp.Value.releaseCreditMeta.pairCount
                }
            }

            $commentShippedCounts = @{}
            if ($repoProp.Value.commentShippedCounts) {
                foreach ($csProp in $repoProp.Value.commentShippedCounts.PSObject.Properties) {
                    $commentShippedCounts[$csProp.Name] = @{
                        shippedClosedCount = [int]$csProp.Value.shippedClosedCount
                        closedPrCount = [int]$csProp.Value.closedPrCount
                    }
                }
            }

            $leaderboardKey = if ($repoProp.Name -match '\|') { $repoProp.Name } else { "$($repoProp.Name)|all" }
            $leaderboards[$leaderboardKey] = @{
                cachedAt = $repoProp.Value.cachedAt
                refreshedAt = $repoProp.Value.refreshedAt
                logins = $logins
                stats = $stats
                shippedCounts = $shippedCounts
                commentShippedCounts = $commentShippedCounts
                releaseCreditCounts = $releaseCreditCounts
                releaseCreditMeta = $releaseCreditMeta
            }
        }
    }

    $contributorsMdSeeds = @{}
    if ($raw.contributorsMdSeeds) {
        foreach ($seedProp in $raw.contributorsMdSeeds.PSObject.Properties) {
            $logins = @()
            if ($seedProp.Value.logins) {
                $logins = @($seedProp.Value.logins | ForEach-Object { [string]$_ })
            }
            $contributorsMdSeeds[$seedProp.Name] = @{
                cachedAt = $seedProp.Value.cachedAt
                logins = $logins
            }
        }
    }

    $prAuthorsByNumber = @{}
    if ($raw.prAuthorsByNumber) {
        foreach ($prop in $raw.prAuthorsByNumber.PSObject.Properties) {
            $prAuthorsByNumber[$prop.Name] = [string]$prop.Value
        }
    }

    $prPullStates = @{}
    if ($raw.prPullStates) {
        foreach ($prop in $raw.prPullStates.PSObject.Properties) {
            $prPullStates[$prop.Name] = @{
                state = [string]$prop.Value.state
                mergedAt = [string]$prop.Value.mergedAt
                title = [string]$prop.Value.title
                author = [string]$prop.Value.author
                body = [string]$prop.Value.body
            }
        }
    }

    $commitCreditMap = @{}
    if ($raw.commitCreditMap) {
        foreach ($repoProp in $raw.commitCreditMap.PSObject.Properties) {
            $credits = @{}
            if ($repoProp.Value.credits) {
                foreach ($lp in $repoProp.Value.credits.PSObject.Properties) {
                    $credits[$lp.Name] = @($lp.Value | ForEach-Object { [int]$_ })
                }
            }
            $commitCreditMap[$repoProp.Name] = @{
                headSha = [string]$repoProp.Value.headSha
                cachedAt = [string]$repoProp.Value.cachedAt
                credits = $credits
            }
        }
    }

    $absorbCommitMap = @{}
    if ($raw.absorbCommitMap) {
        foreach ($repoProp in $raw.absorbCommitMap.PSObject.Properties) {
            $credits = @{}
            if ($repoProp.Value.credits) {
                foreach ($lp in $repoProp.Value.credits.PSObject.Properties) {
                    $credits[$lp.Name] = @($lp.Value | ForEach-Object { [int]$_ })
                }
            }
            $absorbCommitMap[$repoProp.Name] = @{
                headSha = [string]$repoProp.Value.headSha
                cachedAt = [string]$repoProp.Value.cachedAt
                credits = $credits
            }
        }
    }

    $shipCommentClassifications = @{}
    if ($raw.shipCommentClassifications) {
        foreach ($prop in $raw.shipCommentClassifications.PSObject.Properties) {
            $shipCommentClassifications[$prop.Name] = @{
                classification = [string]$prop.Value.classification
                classifiedAt = [string]$prop.Value.classifiedAt
                commentCount = [int]$prop.Value.commentCount
            }
        }
    }

    $mergedPrCreditMap = @{}
    if ($raw.mergedPrCreditMap) {
        foreach ($repoProp in $raw.mergedPrCreditMap.PSObject.Properties) {
            $credits = @{}
            if ($repoProp.Value.credits) {
                foreach ($lp in $repoProp.Value.credits.PSObject.Properties) {
                    $credits[$lp.Name] = @($lp.Value | ForEach-Object { [int]$_ })
                }
            }
            $mergedPrCreditMap[$repoProp.Name] = @{
                headSha = [string]$repoProp.Value.headSha
                cachedAt = [string]$repoProp.Value.cachedAt
                credits = $credits
            }
        }
    }

    $absorbedCreditMap = @{}
    if ($raw.absorbedCreditMap) {
        foreach ($repoProp in $raw.absorbedCreditMap.PSObject.Properties) {
            $credits = @{}
            if ($repoProp.Value.credits) {
                foreach ($lp in $repoProp.Value.credits.PSObject.Properties) {
                    $credits[$lp.Name] = @($lp.Value | ForEach-Object { [int]$_ })
                }
            }
            $absorbedCreditMap[$repoProp.Name] = @{
                changelogSha = [string]$repoProp.Value.changelogSha
                cachedAt = [string]$repoProp.Value.cachedAt
                credits = $credits
            }
        }
    }

    $commitScanMeta = @{}
    if ($raw.commitScanMeta) {
        foreach ($repoProp in $raw.commitScanMeta.PSObject.Properties) {
            $coAuthorIndex = @{}
            if ($repoProp.Value.coAuthorIndex) {
                foreach ($prProp in $repoProp.Value.coAuthorIndex.PSObject.Properties) {
                    $set = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
                    foreach ($login in $prProp.Value) { [void]$set.Add([string]$login) }
                    $coAuthorIndex[[int]$prProp.Name] = $set
                }
            }
            $subjectPrNumbers = @{}
            if ($repoProp.Value.subjectPrNumbers) {
                foreach ($prProp in $repoProp.Value.subjectPrNumbers.PSObject.Properties) {
                    $subjectPrNumbers[[int]$prProp.Name] = @{
                        Sha = [string]$prProp.Value.Sha
                        ParentCount = [int]$prProp.Value.ParentCount
                        HasCoAuthor = [bool]$prProp.Value.HasCoAuthor
                    }
                }
            }
            $normalizedHeadDate = ""
            if ($repoProp.Value.headDate) {
                $hd = $repoProp.Value.headDate
                if ($hd -is [datetime]) { $normalizedHeadDate = $hd.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
                elseif ($hd -match '^\d{4}-\d{2}-\d{2}T') { $normalizedHeadDate = [string]$hd }
                elseif ($hd -as [datetime]) { $normalizedHeadDate = ([datetime]$hd).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
            }
            $commitScanMeta[$repoProp.Name] = @{
                headSha = [string]$repoProp.Value.headSha
                headDate = $normalizedHeadDate
                cachedAt = [string]$repoProp.Value.cachedAt
                coAuthorIndex = $coAuthorIndex
                subjectPrNumbers = $subjectPrNumbers
            }
        }
    }

    return @{
        version = $ClassificationCacheVersion
        entries = $entries
        leaderboards = $leaderboards
        contributorsMdSeeds = $contributorsMdSeeds
        prAuthorsByNumber = $prAuthorsByNumber
        prPullStates = $prPullStates
        commitCreditMap = $commitCreditMap
        absorbCommitMap = $absorbCommitMap
        shipCommentClassifications = $shipCommentClassifications
        mergedPrCreditMap = $mergedPrCreditMap
        absorbedCreditMap = $absorbedCreditMap
        commitScanMeta = $commitScanMeta
    }
}

function Get-PreviousReportTotalPrs([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $content = Read-Utf8TextFile -Path $Path
    } catch {
        return $null
    }
    if ($content -match '<div class="number">(\d+)</div><div class="label">Total PRs</div>') {
        return [int]$Matches[1]
    }
    return $null
}

function Get-CachedAuthorClassificationCount([string[]]$Repos) {
    $count = 0
    foreach ($repo in $Repos) {
        $prefix = "$repo|"
        foreach ($key in $script:ClassificationCache.entries.Keys) {
            if ($key.StartsWith($prefix) -and $key -notmatch '\|shipped-comment\|') {
                $count++
            }
        }
    }
    return $count
}

function Test-GeneratedReportSane(
    [int]$ReportedCount,
    [int]$FetchedCount,
    [int]$AcceptanceClosed,
    [int]$OpenCount,
    [string[]]$FailedRepos,
    [int]$RepoCount,
    [string]$OutFile,
    [int]$CachedClassificationCount = 0
) {
    $issues = @()
    if ($FetchedCount -eq 0) {
        $issues += "GitHub returned 0 author PRs across all repos"
    }
    if ($RepoCount -gt 0 -and $FailedRepos.Count -eq $RepoCount) {
        $issues += "every configured repo PR list fetch failed"
    }
    if ($ReportedCount -eq 0 -and $OpenCount -eq 0) {
        $issues += "report would contain no PRs"
    }
    if ($AcceptanceClosed -eq 0 -and $OpenCount -eq 0) {
        $issues += "acceptance rate would be N/A with no open PRs"
    }

    $previousTotal = Get-PreviousReportTotalPrs -Path $OutFile
    if ($null -ne $previousTotal -and $previousTotal -ge 20 -and $ReportedCount -lt [math]::Floor($previousTotal * 0.5)) {
        $issues += "reported total dropped from $previousTotal to $ReportedCount (>50% loss, likely partial API failure)"
    }
    if ($CachedClassificationCount -ge 50 -and $ReportedCount -lt [math]::Floor($CachedClassificationCount * 0.5)) {
        $issues += "reported total $ReportedCount is far below $CachedClassificationCount cached PR classifications"
    }

    return $issues
}

function Stop-GeneratedReport([string[]]$Issues, [string]$OutFile, [string]$CacheFile) {
    Write-Host "`nRefusing to write report:" -ForegroundColor Red
    foreach ($issue in $Issues) {
        Write-Host "  - $issue" -ForegroundColor Red
    }
    if (Test-Path -LiteralPath $OutFile) {
        Write-Host "Previous page left unchanged: $OutFile" -ForegroundColor Yellow
    }
    Export-ClassificationCache -Path $CacheFile
    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        Read-Host "`nPress Enter to close"
    }
    exit 1
}

function Export-ClassificationCache([string]$Path) {
    $cacheForJson = [ordered]@{
        version = $script:ClassificationCache.version
        entries = [ordered]@{}
        leaderboards = [ordered]@{}
        contributorsMdSeeds = [ordered]@{}
    }

    foreach ($key in ($script:ClassificationCache.entries.Keys | Sort-Object)) {
        if ($key -match '\|shipped-comment\|') { continue }
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

        $releaseCreditCounts = [ordered]@{}
        if ($entry.releaseCreditCounts) {
            foreach ($login in ($entry.releaseCreditCounts.Keys | Sort-Object)) {
                $releaseCreditCounts[$login] = [int]$entry.releaseCreditCounts[$login]
            }
        }

        $releaseCreditMeta = $null
        if ($entry.releaseCreditMeta) {
            $releaseCreditMeta = [ordered]@{
                changelogSha = $entry.releaseCreditMeta.changelogSha
                builtAt = $entry.releaseCreditMeta.builtAt
                pairCount = $entry.releaseCreditMeta.pairCount
            }
        }

        $commentShippedCounts = [ordered]@{}
        if ($entry.commentShippedCounts) {
            foreach ($login in ($entry.commentShippedCounts.Keys | Sort-Object)) {
                $v = $entry.commentShippedCounts[$login]
                $commentShippedCounts[$login] = [ordered]@{
                    shippedClosedCount = [int]$v.shippedClosedCount
                    closedPrCount = [int]$v.closedPrCount
                }
            }
        }

        $cacheForJson.leaderboards[$repo] = [ordered]@{
            cachedAt = $entry.cachedAt
            refreshedAt = $entry.refreshedAt
            logins = @($entry.logins)
            stats = $stats
            shippedCounts = $shippedCounts
            commentShippedCounts = $commentShippedCounts
            releaseCreditCounts = $releaseCreditCounts
            releaseCreditMeta = $releaseCreditMeta
        }
    }

    if ($script:ClassificationCache.contributorsMdSeeds) {
        foreach ($repo in ($script:ClassificationCache.contributorsMdSeeds.Keys | Sort-Object)) {
            $seedEntry = $script:ClassificationCache.contributorsMdSeeds[$repo]
            $cacheForJson.contributorsMdSeeds[$repo] = [ordered]@{
                cachedAt = $seedEntry.cachedAt
                logins = @($seedEntry.logins)
            }
        }
    }

    if ($script:ClassificationCache.prAuthorsByNumber -and $script:ClassificationCache.prAuthorsByNumber.Count -gt 0) {
        $sortedAuthors = [ordered]@{}
        foreach ($key in ($script:ClassificationCache.prAuthorsByNumber.Keys | Sort-Object)) {
            $sortedAuthors[$key] = $script:ClassificationCache.prAuthorsByNumber[$key]
        }
        $cacheForJson["prAuthorsByNumber"] = $sortedAuthors
    }

    if ($script:ClassificationCache.prPullStates -and $script:ClassificationCache.prPullStates.Count -gt 0) {
        $sortedStates = [ordered]@{}
        foreach ($key in ($script:ClassificationCache.prPullStates.Keys | Sort-Object)) {
            $entry = $script:ClassificationCache.prPullStates[$key]
            $sortedStates[$key] = [ordered]@{
                state = [string]$entry.state
                mergedAt = [string]$entry.mergedAt
                title = [string]$entry.title
                author = [string]$entry.author
                body = [string]$entry.body
            }
        }
        $cacheForJson["prPullStates"] = $sortedStates
    }

    foreach ($mapKey in @("commitCreditMap", "absorbCommitMap", "mergedPrCreditMap", "absorbedCreditMap")) {
        $mapData = $script:ClassificationCache[$mapKey]
        if ($mapData -and $mapData.Count -gt 0) {
            $sorted = [ordered]@{}
            foreach ($repo in ($mapData.Keys | Sort-Object)) {
                $entry = $mapData[$repo]
                $credits = [ordered]@{}
                if ($entry.credits) {
                    foreach ($login in ($entry.credits.Keys | Sort-Object)) {
                        $credits[$login] = @($entry.credits[$login] | ForEach-Object { [int]$_ })
                    }
                }
                $obj = [ordered]@{ cachedAt = $entry.cachedAt; credits = $credits }
                if ($entry.headSha) { $obj["headSha"] = $entry.headSha }
                if ($entry.changelogSha) { $obj["changelogSha"] = $entry.changelogSha }
                $sorted[$repo] = $obj
            }
            $cacheForJson[$mapKey] = $sorted
        }
    }

    if ($script:ClassificationCache.shipCommentClassifications -and $script:ClassificationCache.shipCommentClassifications.Count -gt 0) {
        $sorted = [ordered]@{}
        foreach ($key in ($script:ClassificationCache.shipCommentClassifications.Keys | Sort-Object)) {
            $entry = $script:ClassificationCache.shipCommentClassifications[$key]
            $sorted[$key] = [ordered]@{
                classification = [string]$entry.classification
                classifiedAt = [string]$entry.classifiedAt
                commentCount = [int]$entry.commentCount
            }
        }
        $cacheForJson["shipCommentClassifications"] = $sorted
    }

    if ($script:ClassificationCache.commitScanMeta -and $script:ClassificationCache.commitScanMeta.Count -gt 0) {
        $sorted = [ordered]@{}
        foreach ($repo in ($script:ClassificationCache.commitScanMeta.Keys | Sort-Object)) {
            $entry = $script:ClassificationCache.commitScanMeta[$repo]
            $coAuthor = [ordered]@{}
            if ($entry.coAuthorIndex) {
                foreach ($prNum in ($entry.coAuthorIndex.Keys | Sort-Object)) {
                    $coAuthor["$prNum"] = @($entry.coAuthorIndex[$prNum] | Sort-Object)
                }
            }
            $subjects = [ordered]@{}
            if ($entry.subjectPrNumbers) {
                foreach ($prNum in ($entry.subjectPrNumbers.Keys | Sort-Object)) {
                    $info = $entry.subjectPrNumbers[$prNum]
                    $subjects["$prNum"] = [ordered]@{
                        Sha = [string]$info.Sha
                        ParentCount = [int]$info.ParentCount
                        HasCoAuthor = [bool]$info.HasCoAuthor
                    }
                }
            }
            $normalizedHeadDate = ""
            if ($entry.headDate) {
                $hd = $entry.headDate
                if ($hd -is [datetime]) { $normalizedHeadDate = $hd.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
                elseif ($hd -match '^\d{4}-\d{2}-\d{2}T') { $normalizedHeadDate = [string]$hd }
                elseif ($hd -as [datetime]) { $normalizedHeadDate = ([datetime]$hd).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
            }
            $sorted[$repo] = [ordered]@{
                headSha = [string]$entry.headSha
                headDate = $normalizedHeadDate
                cachedAt = [string]$entry.cachedAt
                coAuthorIndex = $coAuthor
                subjectPrNumbers = $subjects
            }
        }
        $cacheForJson["commitScanMeta"] = $sorted
    }

    $json = $cacheForJson | ConvertTo-Json -Depth 8
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
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

    $profileName = $ClassificationTtlProfileFor["$Classification/$EvidenceKind"]
    if (-not $profileName) { $profileName = $ClassificationTtlProfileFor["$Classification/*"] }
    if (-not $profileName) { return $ClosedClassificationCacheTtlHours }

    foreach ($bucket in $ClassificationTtlProfiles[$profileName]) {
        if ($null -eq $bucket[0] -or $ageDays -lt $bucket[0]) { return 24 * $bucket[1] }
    }
    return $ClosedClassificationCacheTtlHours
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
        $stats[$login] = New-LeaderboardStat -Total ([int]$raw.total) -Open ([int]$raw.open) -RecentCount ([int]$raw.recentCount) `
            -LastCreatedAt ([string]$raw.lastCreatedAt) -Now $Now -RateWindowDays $RateWindowDays
    }

    $shippedCounts = @{}
    if ($entry.shippedCounts) {
        foreach ($login in $entry.shippedCounts.Keys) {
            $shippedCounts[$login] = [int]$entry.shippedCounts[$login]
        }
    }

    $refreshedAt = $cachedAt
    if ($entry.refreshedAt) {
        try { $refreshedAt = [datetime]$entry.refreshedAt } catch {}
    }

    $commentShippedCounts = @{}
    if ($entry.commentShippedCounts) {
        if ($entry.commentShippedCounts -is [hashtable]) {
            $commentShippedCounts = $entry.commentShippedCounts
        } else {
            $entry.commentShippedCounts.PSObject.Properties | ForEach-Object {
                $commentShippedCounts[$_.Name] = @{
                    shippedClosedCount = [int]$_.Value.shippedClosedCount
                    closedPrCount = [int]$_.Value.closedPrCount
                }
            }
        }
    }

    $script:LeaderboardCacheHits++
    return @{
        logins = @($entry.logins)
        stats = $stats
        shippedCounts = $shippedCounts
        commentShippedCounts = $commentShippedCounts
        refreshedAt = $refreshedAt
    }
}

function Get-TopCreditedLogins(
    [hashtable]$Stats,
    [int]$Top = $LeaderboardMax
) {
    $refreshLogins = @($Stats.GetEnumerator() |
        Sort-Object { $_.Value.credited }, { $_.Value.open } -Descending |
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
        $entry.stats[$login] = ConvertTo-StoredLeaderboardStat $NewStats[$login]
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
        $storedStats[$login] = ConvertTo-StoredLeaderboardStat $Stats[$login]
    }

    $existingShipped = @{}
    $existingReleaseCreditCounts = @{}
    $existingReleaseCreditMeta = $null
    if ($script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        if ($script:ClassificationCache.leaderboards[$cacheKey].shippedCounts) {
            $existingShipped = $script:ClassificationCache.leaderboards[$cacheKey].shippedCounts
        }
        if ($script:ClassificationCache.leaderboards[$cacheKey].releaseCreditCounts) {
            $existingReleaseCreditCounts = $script:ClassificationCache.leaderboards[$cacheKey].releaseCreditCounts
        }
        if ($script:ClassificationCache.leaderboards[$cacheKey].releaseCreditMeta) {
            $existingReleaseCreditMeta = $script:ClassificationCache.leaderboards[$cacheKey].releaseCreditMeta
        }
    }

    $script:ClassificationCache.leaderboards[$cacheKey] = @{
        cachedAt = (Get-Date).ToString("o")
        refreshedAt = (Get-Date).ToString("o")
        logins = @($Logins | Where-Object { $_ } | Select-Object -Unique)
        stats = $storedStats
        shippedCounts = $existingShipped
        releaseCreditCounts = $existingReleaseCreditCounts
        releaseCreditMeta = $existingReleaseCreditMeta
    }
}

function Set-CachedLeaderboardRefreshedAt([string]$Repo, [Nullable[datetime]]$StartDate) {
    $cacheKey = Get-LeaderboardCacheKey -Repo $Repo -StartDate $StartDate
    if ($script:ClassificationCache.leaderboards.ContainsKey($cacheKey)) {
        $script:ClassificationCache.leaderboards[$cacheKey].refreshedAt = (Get-Date).ToString("o")
    }
}

function Set-CachedLeaderboardShippedCounts(
    [string]$Repo,
    [Nullable[datetime]]$StartDate,
    [hashtable]$ShippedCounts,
    [hashtable]$CommentShippedCounts
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
    if ($CommentShippedCounts -and $CommentShippedCounts.Count -gt 0) {
        $script:ClassificationCache.leaderboards[$cacheKey].commentShippedCounts = $CommentShippedCounts
    }
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
        if (Test-IsPositiveReleaseReferenceToPullRequest -Repo $PullRequest.repo -OriginalPr $PullRequest -ReleasePr $closedEvent.closer) {
            $mergedReleaseCloser = $closedEvent.closer
        }
    }
    $mergedReleaseCrossRefCandidate = Select-BestCrossReference -Candidates @($timelineNodes |
            Where-Object {
                $_.__typename -eq "CrossReferencedEvent" -and
                $_.source.__typename -eq "PullRequest" -and
                $_.source.merged -and
                (Test-IsReleaseTitle $_.source.title)
            }) -ClosedAt $closedEvent.createdAt
    $mergedReleaseCrossRef = $null
    if ($mergedReleaseCrossRefCandidate -and (Test-IsPositiveReleaseReferenceToPullRequest -Repo $PullRequest.repo -OriginalPr $PullRequest -ReleasePr $mergedReleaseCrossRefCandidate)) {
        $mergedReleaseCrossRef = $mergedReleaseCrossRefCandidate
    }
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
    $isShipped = Test-MatchesAnyPattern -Text $comments -Patterns $shippedPatterns
    $isDuplicate = Test-MatchesAnyPattern -Text $comments -Patterns $duplicatePatterns
    $isSuperseded = Test-IsSupersededEvidence -PullRequest $PullRequest -Evidence $raw
    $hasSupersededReference = Test-HasSupersededReference -PullRequest $PullRequest -Evidence $raw
    $isAuthorWithdrawn = Test-IsAuthorWithdrawnEvidence -PullRequest $PullRequest -Evidence $raw
    $acceptedSibling = Get-TimelineCreditedMergedPullRequest -Repo $PullRequest.repo -OriginalPr $PullRequest -Evidence $raw
    if (-not $acceptedSibling -and ($isDuplicate -or $isSuperseded -or $comments)) {
        $acceptedSibling = Get-ReferencedMergedPullRequest -Repo $PullRequest.repo -OriginalPr $PullRequest -Evidence $raw -Text $comments
    }
    $creditedShip = Get-CreditedShipEvidence -PullRequest $PullRequest -Evidence $raw

    $viaLabel = ""
    $viaUrl = ""
    $classification = "lost"
    $evidenceKind = "lost"
    $logLabel = "lost"

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
    } elseif ($acceptedSibling) {
        $classification = "accepted-indirect"
        $evidenceKind = "accepted-indirect"
        $viaLabel = "#$($acceptedSibling.number)"
        $viaUrl = $acceptedSibling.url
        $logLabel = "accepted indirectly via #$($acceptedSibling.number)"
    } elseif ($creditedShip) {
        # Maintainer carried the work forward with the author's credit preserved
        # (co-author trailer / authorship attribution) and shipped it, even
        # though the close was phrased as "superseded"/"integrated".
        $classification = "accepted-indirect"
        $evidenceKind = "accepted-indirect"
        if ($creditedShip.ViaNumber -gt 0) {
            $viaLabel = "#$($creditedShip.ViaNumber)"
            $viaUrl = "https://github.com/$($PullRequest.repo)/pull/$($creditedShip.ViaNumber)"
            $logLabel = "accepted indirectly via #$($creditedShip.ViaNumber) (credited ship)"
        } else {
            $logLabel = "accepted indirectly (credited ship)"
        }
    } elseif ($isSuperseded) {
        $classification = "superseded"
        $evidenceKind = "superseded"
        $logLabel = "superseded"
    } elseif ($isDuplicate) {
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

    Write-ProgressHost "      $Login - classifying $($closed.Count) closed PRs on $repoShort..." -ForegroundColor DarkGray

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

. "$PSScriptRoot\credit-lib.ps1"

if ($VerifyWebuiCreditsOnly) {
    $counts = Get-ChangelogReleaseCreditCounts -Repo "nesquena/hermes-webui" -StartDate $StartDate -Exclusions (Get-RepoLeaderboardExclusions -Repo "nesquena/hermes-webui") -ForceRefresh
    $checks = @(
        @{ login = "franksong2702"; min = 170; max = 200 }
        @{ login = "Michaelyklam"; min = 115; max = 140 }
        @{ login = "rodboev"; min = 115; max = 135 }
        @{ login = "ai-ag2026"; min = 85; max = 110 }
    )
    $failed = $false
    foreach ($check in $checks) {
        $value = if ($counts.ContainsKey($check.login)) { [int]$counts[$check.login] } else { 0 }
        $ok = $value -ge $check.min -and $value -le $check.max
        Write-Host ("{0}: {1} (expected {2}-{3}) {4}" -f $check.login, $value, $check.min, $check.max, $(if ($ok) { "OK" } else { "FAIL" }))
        if (-not $ok) { $failed = $true }
    }
    Export-ClassificationCache -Path $CacheFile
    if ($failed) { exit 1 }
    exit 0
}

Write-Host "Fetching PRs from $($Repos.Count) repos..." -ForegroundColor DarkGray

$allPRs = @()
$repoFetchFailed = @()
$rawFetchedCount = 0
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repo..." -ForegroundColor DarkGray
    $raw = Invoke-Gh pr list --repo $repo --author $Author --state all --limit 500 --json 'number,state,title,createdAt,closedAt,mergedAt,headRefName,author,additions,deletions,changedFiles' 2>$null
    if (-not $raw) {
        $repoFetchFailed += $repo
        Write-Host "    WARNING: PR fetch failed for $repo" -ForegroundColor Yellow
        continue
    }
    $prs = @($raw | ConvertFrom-Json)
    $rawFetchedCount += $prs.Count
    foreach ($pr in $prs) {
        $pr | Add-Member -NotePropertyName repo -NotePropertyValue $repo -Force
        $pr | Add-Member -NotePropertyName repoShort -NotePropertyValue $repoShort -Force
        $pr | Add-Member -NotePropertyName classification -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName release -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName viaLabel -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName viaUrl -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName evidenceKind -NotePropertyValue "" -Force
        $allPRs += $pr
    }
}

$closed = @($allPRs | Where-Object { $_.state -eq "CLOSED" -or $_.state -eq "MERGED" })
$open = @($allPRs | Where-Object { $_.state -eq "OPEN" })
foreach ($pr in $open) {
    $pr.classification = "open"
}

Write-ProgressHost "Classifying $($closed.Count) closed PRs..." -ForegroundColor DarkGray

foreach ($pr in $closed) {
    Write-ProgressHost "  #$($pr.number) ($($pr.repoShort))..." -ForegroundColor DarkGray -NoNewline
    $result = Get-ClosedPullRequestClassification -PullRequest $pr
    $pr.classification = $result.Classification
    $pr.release = $result.Release
    $pr.viaLabel = $result.ViaLabel
    $pr.viaUrl = $result.ViaUrl
    $pr.evidenceKind = $result.EvidenceKind

    $meta = $ClassificationMeta[$result.Classification]
    $color = if ($meta) { $meta.Color } else { "DarkGray" }
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
$reportedPRs = @($allPRs | Where-Object { $_.classification -ne "withdrawn" })

$totalAccepted = $shipped.Count + $acceptedIndirect.Count
$totalNotShipped = $lost.Count + $superseded.Count
$acceptanceClosed = $totalAccepted + $lost.Count + $superseded.Count
$acceptanceRate = if ($acceptanceClosed -gt 0) { [math]::Round(($totalAccepted / $acceptanceClosed) * 100) } else { "N/A" }

if (-not $ForceWrite) {
    $cachedClassificationCount = Get-CachedAuthorClassificationCount -Repos $Repos
    $coreSanityIssues = @(Test-GeneratedReportSane -ReportedCount $reportedPRs.Count -FetchedCount $rawFetchedCount `
        -AcceptanceClosed $acceptanceClosed -OpenCount $open.Count -FailedRepos $repoFetchFailed -RepoCount $Repos.Count `
        -OutFile $OutFile -CachedClassificationCount $cachedClassificationCount)
    if ($coreSanityIssues.Count -gt 0) {
        Stop-GeneratedReport -Issues $coreSanityIssues -OutFile $OutFile -CacheFile $CacheFile
    }
}

$displayRepos = @(Get-ReposByShippedCount -Repos $Repos -PullRequests $reportedPRs)

function Ensure-LeaderboardStatsForLogins(
    [string]$Repo,
    [string[]]$Logins,
    [hashtable]$Stats,
    [object[]]$RecentRepoPRs,
    [datetime]$RateSinceDate,
    [datetime]$Now,
    [double]$RateWindowDays
) {
    $missing = @($Logins | Where-Object { $_ -and -not $Stats.ContainsKey($_) })
    if ($missing.Count -eq 0) {
        return $Stats
    }
    $newStats = Get-LeaderboardStats -Repo $Repo -Logins $missing -RecentRepoPRs $RecentRepoPRs -RateSinceDate $RateSinceDate -Now $Now -RateWindowDays $RateWindowDays
    foreach ($login in $newStats.Keys) {
        $Stats[$login] = $newStats[$login]
    }
    return $Stats
}

# Build per-repo leaderboards
Write-Host "`nBuilding leaderboards..." -ForegroundColor DarkGray
$now = Get-Date
$leaderboardRateSinceDate = $now.AddDays(-$LeaderboardRateWindowDays)
$reportStartLabel = if ($null -ne $startDateValue) { Get-ReportStartLabel -Date $startDateValue } else { "" }

$leaderboardHtml = ""
$authorCreditedByRepo = @{}
foreach ($repo in $displayRepos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repoShort contributors..." -ForegroundColor DarkGray

    $exclusions = Get-RepoLeaderboardExclusions -Repo $repo
    $cachedShippedCounts = @{}
    $cachedCommentShippedCounts = @{}

    $cachedLeaderboard = Get-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -Now $now -RateWindowDays $LeaderboardRateWindowDays -TtlHours $LeaderboardCacheTtlHours -ForceRefresh:$RefreshLeaderboardCache
    $priorLogins = @()
    $repoPRs = $null
    if ($cachedLeaderboard) {
        $stats = $cachedLeaderboard.stats
        if ($cachedLeaderboard.shippedCounts) {
            $cachedShippedCounts = $cachedLeaderboard.shippedCounts
        }
        if ($cachedLeaderboard.commentShippedCounts -and $cachedLeaderboard.commentShippedCounts.Count -gt 0) {
            $cachedCommentShippedCounts = $cachedLeaderboard.commentShippedCounts
        }
        if ($cachedLeaderboard.logins) {
            $priorLogins = @($cachedLeaderboard.logins)
        }

        $cachedCount = if ($cachedLeaderboard.logins.Count -gt 0) { $cachedLeaderboard.logins.Count } else { $stats.Count }
        $refreshAgeHours = ($now - $cachedLeaderboard.refreshedAt).TotalHours
        $newLogins = @()
        $refreshLogins = @()
        if ($refreshAgeHours -gt $LeaderboardRefreshTtlHours) {
            $repoPRs = @(Get-RecentRepoPullRequests -Repo $repo -Limit 500)
            if ($repoPRs.Count -eq 0) { $repoPRs = @() }
            $scannedLogins = Merge-CommunityContributorLogins -Repo $repo -RecentRepoPRs $repoPRs -Exclusions $exclusions -PriorLogins $priorLogins
            $newLogins = @($scannedLogins | Where-Object { -not $stats.ContainsKey($_) })
            if ($newLogins.Count -gt 0) {
                $newStats = Get-LeaderboardStats -Repo $repo -Logins $newLogins -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
                foreach ($login in $newStats.Keys) {
                    $stats[$login] = $newStats[$login]
                }
                Merge-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -NewLogins $newLogins -NewStats $newStats
            }

            $refreshLogins = @(Get-TopCreditedLogins -Stats $stats | Where-Object { $newLogins -notcontains $_ })
            if ($refreshLogins.Count -gt 0) {
                $refreshStats = Get-LeaderboardStats -Repo $repo -Logins $refreshLogins -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
                foreach ($login in $refreshStats.Keys) {
                    $stats[$login] = $refreshStats[$login]
                }
                Merge-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -NewLogins @() -NewStats $refreshStats
            }

            Set-CachedLeaderboardRefreshedAt -Repo $repo -StartDate $StartDate
            $priorLogins = @($priorLogins + $scannedLogins | Select-Object -Unique)
        }

        $logParts = @("$cachedCount contributors from cache")
        if ($newLogins.Count -gt 0) { $logParts += "$($newLogins.Count) new" }
        if ($refreshLogins.Count -gt 0) { $logParts += "top $($refreshLogins.Count) refreshed" }
        if ($refreshAgeHours -le $LeaderboardRefreshTtlHours) { $logParts += "refreshed $([math]::Round($refreshAgeHours))h ago" }
        Write-Host "    $($logParts -join ', ')..." -ForegroundColor DarkGray
    } else {
        if ($null -eq $repoPRs) { $repoPRs = @(Get-RecentRepoPullRequests -Repo $repo -Limit 500) }
        if ($repoPRs.Count -eq 0) { continue }
        $scannedLogins = Merge-CommunityContributorLogins -Repo $repo -RecentRepoPRs $repoPRs -Exclusions $exclusions -PriorLogins @()

        Write-Host "    $($scannedLogins.Count) community contributors found, fetching batched counts..." -ForegroundColor DarkGray

        $stats = Get-LeaderboardStats -Repo $repo -Logins $scannedLogins -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
        Set-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -Logins $scannedLogins -Stats $stats
        $priorLogins = @($scannedLogins)
    }

    $missing = @($priorLogins | Where-Object { $_ -and -not $stats.ContainsKey($_) })
    if ($missing.Count -gt 0) {
        if ($null -eq $repoPRs) { $repoPRs = @(Get-RecentRepoPullRequests -Repo $repo -Limit 500) }
        if ($repoPRs.Count -gt 0) {
            $stats = Ensure-LeaderboardStatsForLogins -Repo $repo -Logins $priorLogins -Stats $stats -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
        }
    }

    # Ensure the author appears in stats even when the leaderboard cache is fresh.
    if ($Author -and -not $stats.ContainsKey($Author)) {
        $myRepoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count + @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
        $myRepoOpen = @($open | Where-Object { $_.repo -eq $repo }).Count
        if ($myRepoShipped -gt 0 -or $myRepoOpen -gt 0) {
            if ($null -eq $repoPRs) { $repoPRs = @(Get-RecentRepoPullRequests -Repo $repo -Limit 500) }
            $authorStats = Get-LeaderboardStats -Repo $repo -Logins @($Author) -RecentRepoPRs $repoPRs -RateSinceDate $leaderboardRateSinceDate -Now $now -RateWindowDays $LeaderboardRateWindowDays
            if ($authorStats.ContainsKey($Author)) {
                $stats[$Author] = $authorStats[$Author]
                Merge-CachedLeaderboardStats -Repo $repo -StartDate $StartDate -NewLogins @($Author) -NewStats $authorStats
            }
        }
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
            estimated = $true
            shippedClassified = $false
        }
    }

    if ($communityStats.Count -eq 0) { continue }

    $myRepoCredited = @($shipped | Where-Object { $_.repo -eq $repo }).Count + @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $myRepoOpen = @($open | Where-Object { $_.repo -eq $repo }).Count
    $creditProfile = Get-RepoCreditProfile -Repo $repo

    $useCachedShipped = (-not $RefreshLeaderboardCache) -and $creditProfile -ne "changelog-release"
    $creditResult = Apply-LeaderboardCreditedCounts -Repo $repo -CommunityStats $communityStats -Author $Author -MyRepoCredited $myRepoCredited -MyRepoOpen $myRepoOpen `
        -CachedShippedCounts $cachedShippedCounts -CachedCommentShippedCounts $cachedCommentShippedCounts -Exclusions $exclusions -UseCachedShipped:$useCachedShipped -StartDate $StartDate `
        -ClassifyTop $LeaderboardClassifyTop -ForceRefresh:$RefreshLeaderboardCache
    $shippedCountsToSave = $creditResult.ShippedCountsToSave
    $commentShippedCounts = $creditResult.CommentShippedCounts
    $classifiedFromCache = $creditResult.ClassifiedFromCache
    $classifiedLive = $creditResult.ClassifiedLive

    if ($classifiedFromCache -gt 0 -or $classifiedLive -gt 0) {
        if ($creditProfile -eq "changelog-release") {
            Write-Host "    Release credits applied to $($communityStats.Count) contributors" -ForegroundColor DarkGray
        } else {
            Write-Host "    $classifiedFromCache credited totals from cache, $classifiedLive newly classified" -ForegroundColor DarkGray
        }
    }
    Set-CachedLeaderboardShippedCounts -Repo $repo -StartDate $StartDate -ShippedCounts $shippedCountsToSave -CommentShippedCounts $commentShippedCounts
    Export-ClassificationCache -Path $CacheFile

    $allSorted = @($communityStats.GetEnumerator() | Sort-Object { $_.Value.credited }, { $_.Value.open } -Descending)
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
    if ($myRank -gt $DefaultLeaderboardVisible -and $myRank -le $totalContributors) {
        $collapseMode = "context"
        $halfWindow = [math]::Floor($DefaultLeaderboardVisible / 2)
        $visibleStart = [math]::Max(1, $myRank - $halfWindow)
        $visibleEnd = [math]::Min($totalContributors, $visibleStart + $DefaultLeaderboardVisible - 1)
        if ($visibleEnd -eq $totalContributors) { $visibleStart = [math]::Max(1, $visibleEnd - $DefaultLeaderboardVisible + 1) }
    } else {
        $collapseMode = "top"
        $visibleStart = 1
        $visibleEnd = [math]::Min($DefaultLeaderboardVisible, $totalContributors)
    }
    $expandAfterRank = if ($collapseMode -eq "context") { $visibleEnd } else { $DefaultLeaderboardVisible }
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
        $leaderboardRows += "  <tr$rowClass data-rank=`"$rank`"><td>#$rank</td><td><a href=`"https://github.com/$name`">$name</a></td><td>$($s.credited)</td><td>$($s.open)</td><td>$($s.rate)/d</td><td><span class=`"$statusClass`">$statusLabel</span></td></tr>`n"
        if ($rank -eq $expandAfterRank -and $totalContributors -gt $DefaultLeaderboardVisible) {
            $leaderboardRows += Get-ExpandRowHtml "lb-$repoShort" $expandLabel
        }
        $rank++
    }

    # Build projections for contributors ahead of me
    $projectionsHtml = ""
    $myCredited = if ($communityStats.ContainsKey($Author)) { $communityStats[$Author].credited } else { 0 }
    $myRate = if ($communityStats.ContainsKey($Author)) { $communityStats[$Author].rate } else { 0 }
    $authorCreditedByRepo[$repo] = $myCredited
    $ahead = @($allSorted | Where-Object { $_.Value.credited -gt $myCredited })

    $projBody = $null
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
        $projBody = "<table>`n  <tr><th>Contributor</th><th>Shipped</th><th>Rate (7d)</th><th>Catch-up</th></tr>`n$projRows</table>"
    }
    if ($projBody) {
        $projectionsHtml = @"
<details class="projections">
<summary>Projections ($Author @ $myRate/day Rate (7d), rank #$myRank)</summary>
$projBody
</details>
"@
    }

    $collapsedClass = if ($totalContributors -gt $DefaultLeaderboardVisible) { " collapsed" } else { "" }
    $overlayHtml = if ($totalContributors -gt $DefaultLeaderboardVisible) { Get-CollapseOverlayHtml "lb-$repoShort" } else { "" }
    $topCollapseAttrs = if ($collapseMode -eq "top") { " data-visible-items=`"$DefaultLeaderboardVisible`" data-rows-per-item=`"1`"" } else { "" }
    $leaderboardHtml += @"
<h2>$repoShort Community Leaderboard</h2>
<div class="collapsible-table leaderboard$collapsedClass" id="lb-$repoShort" data-collapse-mode="$collapseMode"$topCollapseAttrs>
<table>
  <thead><tr><th>Rank</th><th>Contributor</th><th>Shipped</th><th>Open</th><th>Rate (7d)</th><th>Status</th></tr></thead>
  <tbody>
$leaderboardRows  </tbody>
</table>
$overlayHtml
</div>
$projectionsHtml

"@
}


Export-ClassificationCache -Path $CacheFile

if (Test-Path -LiteralPath $ReadmePath) {
    Write-Host "`nReading representative PRs from $ReadmePath..." -ForegroundColor DarkGray
    $readmeText = Read-Utf8TextFile -Path $ReadmePath
} else {
    Write-Host "`nFetching representative PRs from $ReadmeRepo README..." -ForegroundColor DarkGray
    $readmeB64 = Invoke-Gh api "repos/$ReadmeRepo/contents/README.md" --jq '.content' 2>$null
    $readmeText = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(($readmeB64 -replace "\s","")))
}

function Format-LinkedLabel([string]$Label, [string]$Url) {
    if ($Label -and $Url) {
        return "<a href=`"$Url`">$Label</a>"
    }
    if ($Label) {
        return $Label
    }
    return ""
}

function Format-RepresentativeReleaseCell(
    [string]$Release,
    [string]$ReleaseUrl,
    [string]$Classification
) {
    $cell = Format-LinkedLabel -Label $Release -Url $ReleaseUrl
    if ($cell) { return $cell }
    if ($Classification -eq "accepted-indirect") { return "indirect" }
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
        $viaCell = Format-LinkedLabel -Label $m.viaLabel -Url $m.viaUrl
        $representativeHtml += "  <tr class=`"rep-main-row`"><td><a href=`"$($m.url)`">#$($m.num)</a></td><td>$($m.repoLabel)</td><td class=`"rep-desc-cell`">$($m.desc)</td><td>$releaseCell</td><td>$viaCell</td></tr>`n"
        $representativeHtml += "  <tr class=`"rep-desc-row`"><td class=`"rep-desc-gap`"></td><td colspan=`"4`"><div class=`"rep-desc-text`">$($m.desc)</div></td></tr>`n"
    }
    $representativeHtml += "</table>"
} else {
    $representativeHtml = '<p class="empty-state">Representative PRs unavailable.</p>'
}

Write-Host "Generating HTML..." -ForegroundColor DarkGray

$allPRItems = @()
foreach ($pr in $reportedPRs) {
    $classificationKey = if ($pr.classification) { $pr.classification } else { "open" }
    $statusKey = if ($classificationKey -eq "accepted-indirect") { "shipped" } else { $classificationKey }
    $meta = $ClassificationMeta[$classificationKey]
    if (-not $meta) { $meta = $ClassificationMeta["open"] }
    $statusLabel = $meta.Label
    $statusClass = $meta.Tag
    $releaseLabel = if ($classificationKey -eq "accepted-indirect") { "indirect" }
        elseif ($classificationKey -eq "shipped" -and $pr.release) { $pr.release }
        else { "" }

    $effectiveDate = Get-PullRequestEffectiveIsoDate -PullRequest $pr -StatusKey $statusKey
    $sortDate = if ($effectiveDate) { [datetime]$effectiveDate } else { [datetime]::MinValue }
    $displayDate = Format-EasternDate $effectiveDate

    $allPRItems += [pscustomobject][ordered]@{
        number = $pr.number
        repo = $pr.repo
        repoLabel = Get-RepoLabel -Repo $pr.repo
        title = $pr.title
        classification = $classificationKey
        statusKey = $statusKey
        statusLabel = $statusLabel
        statusClass = $statusClass
        dateLabel = $displayDate
        releaseLabel = Get-ScalarValue $releaseLabel
        viaLabel = Get-ScalarValue $pr.viaLabel
        viaUrl = Get-ScalarValue $pr.viaUrl
        createdAt = Get-ScalarValue $pr.createdAt
        closedAt = Get-ScalarValue $pr.closedAt
        mergedAt = Get-ScalarValue $pr.mergedAt
        additions = [int](Get-ScalarValue $pr.additions)
        deletions = [int](Get-ScalarValue $pr.deletions)
        changedFiles = [int](Get-ScalarValue $pr.changedFiles)
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
        return $ItemStatusKey -in @("lost", "superseded")
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
    $pillCount = $filter.count
    $prFilterPills += "    <div class=`"sort-pill$activeClass`" data-status=`"$($filter.key)`">$($filter.label) ($pillCount)</div>`n"
}

$prRepoFilters = @(
    [pscustomobject][ordered]@{ key = "all"; label = "All" }
)
foreach ($repo in $displayRepos) {
    $repoLabel = Get-RepoLabel -Repo $repo
    $prRepoFilters += [pscustomobject][ordered]@{ key = $repoLabel; label = $repoLabel }
}

$prRepoPills = ""
foreach ($filter in $prRepoFilters) {
    $activeClass = if ($filter.key -eq $defaultPrRepoKey) { " active" } else { "" }
    $prRepoPills += "    <div class=`"sort-pill$activeClass`" data-repo=`"$($filter.key)`">$($filter.label)</div>`n"
}

$prDataJson = (@(
    $allPRItems | ForEach-Object {
        [pscustomobject][ordered]@{
            number = $_.number
            url = "https://github.com/$($_.repo)/pull/$($_.number)"
            repo = $_.repo
            repoLabel = $_.repoLabel
            title = $_.title
            classification = $_.classification
            statusKey = $_.statusKey
            statusLabel = $_.statusLabel
            statusClass = $_.statusClass
            dateLabel = $_.dateLabel
            releaseLabel = $_.releaseLabel
            viaLabel = $_.viaLabel
            viaUrl = $_.viaUrl
            createdAt = $_.createdAt
            closedAt = $_.closedAt
            mergedAt = $_.mergedAt
            additions = $_.additions
            deletions = $_.deletions
            changedFiles = $_.changedFiles
        }
    }
) | ConvertTo-Json -Depth 4 -Compress) -replace '</script', '<\/script'
$prFiltersJson = (@($prStatusFilters) | ConvertTo-Json -Compress) -replace '</script', '<\/script'

$repoSections = ""
foreach ($repo in $displayRepos) {
    $repoShort = ($repo -split '/')[-1]
    $repoPRs = @($reportedPRs | Where-Object { $_.repo -eq $repo })
    $repoCounts = @{}
    foreach ($group in ($repoPRs | Group-Object classification)) { $repoCounts[$group.Name] = $group.Count }

    $hasCherryPick = ($repoPRs | Where-Object { $_.evidenceKind -eq "timeline" }).Count -gt 0
    $hasIndirect = ($repoPRs | Where-Object { $_.evidenceKind -eq "accepted-indirect" }).Count -gt 0
    $shippedDesc = if ($hasCherryPick -and $hasIndirect) { "Merged, cherry-picked, and release-credited" }
        elseif ($hasCherryPick) { "Merged and cherry-picked" }
        elseif ($hasIndirect) { "Merged and release-credited" }
        else { "Merged" }

    $repoStatusRows = ""
    foreach ($status in @("shipped", "open", "superseded", "lost")) {
        $count = if ($status -eq "shipped") { [int]$repoCounts["shipped"] + [int]$repoCounts["accepted-indirect"] }
            else { [int]$repoCounts[$status] }
        if ($count -eq 0 -and $status -notin @("shipped", "open")) { continue }
        $meta = $ClassificationMeta[$status]
        $desc = if ($status -eq "shipped") { $shippedDesc } else { $meta.Desc }
        $repoStatusRows += "  <tr><td><span class=`"tag $($meta.Tag)`">$($meta.Label)</span></td><td>$count</td><td>$desc</td></tr>`n"
    }

    $repoSections += @"
<h2>$repoShort ($($repoPRs.Count) PRs)</h2>
<table class="repo-status">
  <tr><th>Status</th><th>Count</th><th>Details</th></tr>
$repoStatusRows</table>

"@
}

$dateStr = $now.ToString("MMMM d, yyyy")

# Calculate active days (distinct dates with PR activity)
$allDates = @()
foreach ($pr in $reportedPRs) {
    $statusKey = if ($pr.classification) { $pr.classification } else { "open" }
    $effectiveDate = Get-PullRequestEffectiveIsoDate -PullRequest $pr -StatusKey $statusKey
    if ($effectiveDate) { try { $allDates += [datetime]$effectiveDate } catch {} }
}
$allDates = @($allDates | Sort-Object)
if ($allDates.Count -ge 2) {
    $activeDays = @($allDates | ForEach-Object { $_.Date } | Sort-Object -Unique).Count
    $timeSpan = if ($activeDays -eq 1) { "1 day" } else { "$activeDays days" }
    $displayEndDate = $allDates[0].AddDays([math]::Floor(($allDates[-1] - $allDates[0]).TotalDays))
    $timeRange = "Active days from $($allDates[0].ToString('MMM d')) - $($displayEndDate.ToString('MMM d'))"
} else {
    $timeSpan = "N/A"
    $timeRange = ""
}

# Title/Content: when the segment renders a title attribute / inline count ("wide" = only when > 4%)
$barTotal = $reportedPRs.Count
$barSegments = @(
    @{ Key = "shipped";    Label = "Shipped";    Count = $totalAccepted;     Title = "wide";   Content = "wide" }
    @{ Key = "superseded"; Label = "Superseded"; Count = $superseded.Count;  Title = "never";  Content = "wide" }
    @{ Key = "lost";       Label = "Lost";       Count = $lost.Count;        Title = "never";  Content = "wide" }
    @{ Key = "open";       Label = "Open";       Count = $open.Count;        Title = "always"; Content = "always" }
)
$barHtml = ""
$legendHtml = ""
foreach ($seg in $barSegments) {
    $pct = if ($barTotal -gt 0) { [math]::Round(($seg.Count / $barTotal) * 100, 1) } else { 0 }
    $wide = $pct -gt 4
    $title = if ($seg.Title -eq "always" -or ($seg.Title -eq "wide" -and $wide)) { " title=`"$($seg.Count)`"" } else { "" }
    $content = if ($seg.Content -eq "always" -or $wide) { $seg.Count } else { "" }
    $barHtml += "  <div class=`"bar-segment bar-$($seg.Key)`" id=`"bd-bar-$($seg.Key)`" data-width=`"$pct`"$title>$content</div>`n"
    $legendHtml += "  <div class=`"legend-item`" id=`"bd-leg-$($seg.Key)`"><div class=`"legend-dot legend-dot-$($seg.Key)`"></div> $($seg.Label) ($($seg.Count))</div>`n"
}

$prListOverlayHtml = Get-CollapseOverlayHtml "pr-list-collapsible"

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
    <meta name="color-scheme" content="light dark" />
    <link rel="stylesheet" href="../style.css?v=20260616b">
  </head>
<body class="pr">

<div class="top-row">
  <h1><a class="back-link" href="../"><svg viewBox="0 0 16 16" width="1em" height="1em"><path d="M10 2L4 8l6 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>GitHub Contributions</h1>
  <nav class="nav-links">
    <a href="../resume/">Resume</a>
    <span class="nav-sep">/</span>
    <a href="../projects/">Projects</a>
    <span class="nav-sep">/</span>
    <span class="current">Stats</span>
    <span class="nav-sep">/</span>
    <a href="https://github.com/rodboev">GitHub</a>
    <span class="nav-sep">/</span>
    <a href="https://linkedin.com/in/rodboev">LinkedIn</a>
  </nav>
</div>


<h2>Breakdown</h2>

<div class="grid grid-summary">
  <div class="stat-card"><div class="number" id="bd-total">$($reportedPRs.Count)</div><div class="label">Total PRs</div></div>
  <div class="stat-card"><div class="number green" id="bd-shipped">$totalAccepted</div><div class="label">Shipped</div></div>
  <div class="stat-card"><div class="number yellow" id="bd-open">$($open.Count)</div><div class="label">Open</div></div>
  <div class="stat-card"><div class="number" id="bd-lost-sup">$totalNotShipped</div><div class="label">Lost/Superseded</div></div>
</div>
<div class="grid grid-summary">
  <div class="stat-card"><div class="number green" id="bd-rate">${acceptanceRate}%</div><div class="label" id="bd-rate-label">Acceptance ($($superseded.Count) superseded, $($lost.Count) lost)</div></div>
  <div class="stat-card"><div class="number blue" id="bd-days">$timeSpan</div><div class="label" id="bd-days-label">$timeRange</div></div>
</div>

<div class="bar-container">
$barHtml</div>
<div class="legend">
$legendHtml</div>

$repoSections

$leaderboardHtml

$representativeHtml

<div class="landscape-row" id="pr-landscape-row">
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
<div class="collapsible-table collapsed" id="pr-list-collapsible" data-collapse-mode="top" data-visible-items="$DefaultPrTableVisible" data-rows-per-item="2">
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
$prListOverlayHtml
</div>

<h2>Methodology</h2>
<div class="section methodology-section">
  <p><strong>Shipped</strong> means maintainer-accepted work: a direct merge, timeline evidence such as a release PR or cherry-pick reference, or an indirect landing credited back to the original PR. GitHub's <code>mergedAt</code> flag alone is not sufficient because some repos land contributor work outside the merge button.</p>
  <p><strong>Withdrawn</strong>, <strong>superseded</strong>, and <strong>lost</strong> separate author pullbacks, maintainer replacements, and competing outcomes. The PR table is sorted newest-first using close time for closed PRs and creation time for open PRs.$(if ($null -ne $startDateValue) { " This report includes PRs with an effective date on or after $reportStartLabel." })</p>
  <p><strong>Community leaderboards</strong> rank third-party contributors only. Repo owners, org employees, collaborators with write access, integration bots, and other automated accounts are excluded. Each board shows the top $LeaderboardMax contributors. Contributor discovery unions recent repo activity, prior cache entries, and (for hermes-webui) names from upstream <code>CONTRIBUTORS.md</code>. <strong>hermes-webui Credited</strong> counts distinct release-attributed PRs from <code>CHANGELOG.md</code> (including <code>(#123, @user)</code> lines), filtered contributor merges, and release-commit <code>Co-authored-by</code> trailers (rebuilt when the changelog changes). Other repos use evidence-based shipped classification for the top $LeaderboardClassifyTop contributors and merged-PR totals as a proxy below that. <strong>Rate (7d)</strong> is PRs opened in the rolling last $LeaderboardRateWindowDays days divided by $LeaderboardRateWindowDays (a recent per-day opening rate, not merge velocity).</p>
</div>

<p class="footer">Generated $dateStr from GitHub API. Source: <a href="https://github.com/$ReadmeRepo">$ReadmeRepo</a></p>

<script src="../assets/script.js"></script>
<script>
var PR_FILTERS = $prFiltersJson;
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
  var row = document.getElementById('pr-landscape-row');
  if (!row) return;
  document.body.style.setProperty('--landscape-row-offset', row.getBoundingClientRect().height + 'px');
}
function prMatchesStatus(item, statusKey) {
  if (statusKey === 'not-shipped') {
    return item.statusKey === 'lost' || item.statusKey === 'superseded';
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
  var wrapper = document.getElementById('pr-list-collapsible');
  if (!tbody) return;
  var filtered = PR_DATA.filter(function(item) {
    return prMatchesStatus(item, statusKey) && prMatchesRepo(item, repoKey);
  });
  var visibleItems = collapsibleVisibleItems(wrapper);
  var html = '';
  filtered.forEach(function(item, index) {
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
    if (wrapper && index + 1 === visibleItems && filtered.length > visibleItems) {
      html += expandRowHtml(wrapper.id, 'Show all ' + filtered.length, 6);
    }
  });
  if (!html) {
    html = '<tr><td colspan="6" class="empty-state">No PRs in this status.</td></tr>';
  }
  tbody.innerHTML = html;
  setCollapsibleCollapsed(wrapper, filtered.length > visibleItems);
  updateCollapsedOverlays();
}
function bindPillGroup(containerId, attr, filterKey) {
  document.getElementById(containerId).addEventListener('click', function(e) {
    var pill = e.target.closest('.sort-pill');
    if (!pill) return;
    CURRENT_PR_FILTER[filterKey] = pill.getAttribute(attr);
    document.querySelectorAll('#' + containerId + ' .sort-pill').forEach(function(p) {
      p.classList.remove('active');
    });
    pill.classList.add('active');
    updatePrFilterPills();
    renderPrTable(CURRENT_PR_FILTER.statusKey, CURRENT_PR_FILTER.repoKey);
  });
}
bindPillGroup('pr-filter-pills', 'data-status', 'statusKey');
bindPillGroup('pr-repo-pills', 'data-repo', 'repoKey');
setBarWidths();
syncLandscapeStickyOffset();
if (typeof ResizeObserver !== 'undefined') {
  var landscapeRow = document.getElementById('pr-landscape-row');
  if (landscapeRow) {
    new ResizeObserver(syncLandscapeStickyOffset).observe(landscapeRow);
  }
}
window.addEventListener('resize', syncLandscapeStickyOffset);
updatePrFilterPills();
renderPrTable(CURRENT_PR_FILTER.statusKey, CURRENT_PR_FILTER.repoKey);

document.querySelectorAll('details.projections').forEach(function(details) {
  var lb = details.previousElementSibling;
  while (lb && !lb.classList.contains('leaderboard')) lb = lb.previousElementSibling;
  if (!lb) return;
  var selfRow = lb.querySelector('tr.is-self');
  if (!selfRow) return;
  var cells = selfRow.querySelectorAll('td');
  var myOpen = parseInt(cells[3].textContent) || 0;
  if (!myOpen) return;
  var summaryEl = details.querySelector('summary');
  var myRate = parseFloat((summaryEl.textContent.match(/([\d.]+)\/day/) || [])[1]) || 0;
  if (!myRate) return;
  var myCredited = parseInt(cells[2].textContent) || 0;
  details.querySelectorAll('table tr').forEach(function(row) {
    var td = row.querySelectorAll('td');
    if (td.length < 4) return;
    var name = td[0].textContent.trim();
    var lbRow = null;
    lb.querySelectorAll('tbody tr').forEach(function(r) {
      var a = r.querySelector('a');
      if (a && a.textContent.trim() === name) lbRow = r;
    });
    var theirOpen = lbRow ? (parseInt(lbRow.querySelectorAll('td')[3].textContent) || 0) : 0;
    var theirRate = parseFloat(td[2].textContent) || 0;
    var rawGap = parseInt((td[1].textContent.match(/\+(\d+)/) || [])[1]) || 0;
    var effectiveGap = rawGap - myOpen + theirOpen;
    var netRate = myRate - theirRate;
    var catchupTd = td[3];
    if (effectiveGap <= 0 && myRate > 0) {
      var days = Math.round(rawGap / myRate * 10) / 10;
      var when = new Date(Date.now() + days * 86400000);
      var dateStr = when.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      var pending = myOpen - rawGap + theirOpen;
      catchupTd.className = '';
      catchupTd.innerHTML = days + 'd (' + dateStr + ') <span class="note">(' + pending + ' surplus)</span>';
    } else if (effectiveGap > 0 && netRate > 0) {
      var days = Math.round(effectiveGap / netRate * 10) / 10;
      var when = new Date(Date.now() + days * 86400000);
      var dateStr = when.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      catchupTd.innerHTML = days + 'd (' + dateStr + ') <span class="note">(' + myOpen + ' pending)</span>';
    }
  });
  summaryEl.textContent = summaryEl.textContent.replace(/rank #/, myOpen + ' open, rank #');
});
</script>

</body>
</html>
"@

if (-not $ForceWrite) {
    $finalSanityIssues = @(Test-GeneratedReportSane -ReportedCount $reportedPRs.Count -FetchedCount $rawFetchedCount `
        -AcceptanceClosed $acceptanceClosed -OpenCount $open.Count -FailedRepos $repoFetchFailed -RepoCount $Repos.Count `
        -OutFile $OutFile -CachedClassificationCount $cachedClassificationCount)
    if ($finalSanityIssues.Count -gt 0) {
        Stop-GeneratedReport -Issues $finalSanityIssues -OutFile $OutFile -CacheFile $CacheFile
    }
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($OutFile, $html, $utf8NoBom)
Write-Host "`nWritten to $OutFile" -ForegroundColor Green
Write-Host "  Total: $($reportedPRs.Count) | Shipped: $totalAccepted | Open: $($open.Count) | Lost: $($lost.Count) | Rate: ${acceptanceRate}%"
Write-Host "  Closed classification cache hits: $script:ClassificationCacheHits | Leaderboard cache hits: $script:LeaderboardCacheHits | Cache file: $CacheFile" -ForegroundColor DarkGray
$generateElapsed = (Get-Date) - $script:GenerateStartedAt
Write-Host "  Elapsed: $([int]$generateElapsed.TotalSeconds)s ($([math]::Round($generateElapsed.TotalMinutes, 1)) min)" -ForegroundColor DarkGray

$timelinePy = Join-Path $PSScriptRoot "generate-timeline.py"
if (Test-Path $timelinePy) {
    Write-Host "`nInjecting timeline chart..." -ForegroundColor Cyan
    python $timelinePy
}

if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    Read-Host "`nPress Enter to close"
}
