param(
    [string]$Author = "rodboev",
    [string[]]$Repos = @("nesquena/hermes-webui", "NousResearch/hermes-agent"),
    [string]$ReadmeRepo = "rodboev/pr-sweep",
    [string]$OutFile = "$PSScriptRoot\index.html",
    [int]$LeaderboardTop = 10,
    [switch]$OpenOutput
)

$shippedPatterns = @("Shipped", "shipped", "cherry-picked", "merged-via", "Salvaged into", "salvaged into")
$acceptedPatterns = @()
$duplicatePatterns = @("Duplicate", "duplicate")
$lostPatterns = @("Superseded by", "superseded by", "consolidated", "Consolidating")

$EasternTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")

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

Write-Host "Fetching PRs from $($Repos.Count) repos..." -ForegroundColor DarkGray

$allPRs = @()
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repo..." -ForegroundColor DarkGray
    $prs = gh pr list --repo $repo --author $Author --state all --limit 500 --json number,state,title,createdAt,closedAt,mergedAt,headRefName 2>$null | ConvertFrom-Json
    foreach ($pr in $prs) {
        $pr | Add-Member -NotePropertyName repo -NotePropertyValue $repo -Force
        $pr | Add-Member -NotePropertyName repoShort -NotePropertyValue $repoShort -Force
        $pr | Add-Member -NotePropertyName classification -NotePropertyValue "" -Force
        $pr | Add-Member -NotePropertyName release -NotePropertyValue "" -Force
        $allPRs += $pr
    }
}

$closed = @($allPRs | Where-Object { $_.state -eq "CLOSED" })
$open = @($allPRs | Where-Object { $_.state -eq "OPEN" })

Write-Host "Classifying $($closed.Count) closed PRs..." -ForegroundColor DarkGray

$shipped = @(); $acceptedIndirect = @(); $duplicates = @(); $lost = @(); $withdrawn = @(); $rejected = @()

foreach ($pr in $closed) {
    Write-Host "  #$($pr.number) ($($pr.repoShort))..." -ForegroundColor DarkGray -NoNewline
    $raw = gh pr view $pr.number --repo $pr.repo --json comments 2>$null | ConvertFrom-Json
    $comments = ($raw.comments | Where-Object { $_.author.login -ne "greptile-apps" } | ForEach-Object { $_.body }) -join "`n---`n"

    $release = ""
    if ($comments -match "v\d+\.\d+\.\d+") { $release = $Matches[0] }
    $pr.release = $release

    $isShipped = $false
    foreach ($p in $shippedPatterns) { if ($comments -match [regex]::Escape($p)) { $isShipped = $true; break } }

    $isAccepted = $false
    foreach ($p in $acceptedPatterns) { if ($comments -match [regex]::Escape($p)) { $isAccepted = $true; break } }

    $isDuplicate = $false
    foreach ($p in $duplicatePatterns) { if ($comments -match [regex]::Escape($p)) { $isDuplicate = $true; break } }

    $isLost = $false
    foreach ($p in $lostPatterns) { if ($comments -match [regex]::Escape($p)) { $isLost = $true; break } }

    if ($isShipped) {
        $pr.classification = "shipped"
        $shipped += $pr
        Write-Host " shipped" -ForegroundColor Green
    } elseif ($isAccepted) {
        $pr.classification = "accepted-indirect"
        $acceptedIndirect += $pr
        Write-Host " accepted (indirect)" -ForegroundColor Cyan
    } elseif ($isDuplicate -or $isLost) {
        $pr.classification = "lost"
        $lost += $pr
        Write-Host " lost (competing PR won)" -ForegroundColor Red
    } elseif (-not $comments -or $comments.Trim().Length -eq 0) {
        $pr.classification = "withdrawn"
        $withdrawn += $pr
        Write-Host " withdrawn (no maintainer interaction)" -ForegroundColor DarkGray
    } else {
        $pr.classification = "withdrawn"
        $withdrawn += $pr
        Write-Host " withdrawn" -ForegroundColor DarkGray
    }
}

$totalAccepted = $shipped.Count + $acceptedIndirect.Count
$totalResolved = $totalAccepted + $lost.Count
$acceptanceRate = if ($totalResolved -gt 0) { [math]::Round(($totalAccepted / $totalResolved) * 100) } else { "N/A" }

# Build per-repo leaderboards
Write-Host "`nBuilding leaderboards..." -ForegroundColor DarkGray
$now = Get-Date
$jun1 = [datetime]"2026-06-01"
$daysSinceJun1 = ($now - $jun1).TotalDays

$leaderboardHtml = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repoShort contributors..." -ForegroundColor DarkGray

    # Discover unique authors from the most recent 500 PRs, then fetch full counts per-author
    $raw = gh pr list --repo $repo --state all --limit 500 --json author 2>$null
    if (-not $raw) { continue }
    $repoPRs = @(($raw | ConvertFrom-Json) | ForEach-Object { $_ })
    $uniqueLogins = @($repoPRs | ForEach-Object { $_.author.login } | Where-Object { $_ -and $_ -ne "nesquena-hermes" } | Select-Object -Unique)
    if ($uniqueLogins -notcontains $Author) { $uniqueLogins = @($Author) + $uniqueLogins }

    Write-Host "    $($uniqueLogins.Count) contributors found, fetching per-author..." -ForegroundColor DarkGray

    $stats = @{}
    foreach ($a in $uniqueLogins) {
        $aRaw = gh pr list --repo $repo --author $a --state all --limit 500 --json number,createdAt,state 2>$null
        if (-not $aRaw) { continue }
        $prs = @(($aRaw | ConvertFrom-Json) | ForEach-Object { $_ })
        if ($prs.Count -eq 0) { continue }

        $dates = @()
        foreach ($pr in $prs) {
            if ($pr.createdAt) { try { $dates += [datetime]$pr.createdAt } catch {} }
        }
        $dates = @($dates | Sort-Object)

        $closedCount = @($prs | Where-Object { $_.state -eq "CLOSED" }).Count
        $openCount = @($prs | Where-Object { $_.state -eq "OPEN" }).Count
        $mergedCount = @($prs | Where-Object { $_.state -eq "MERGED" }).Count

        $sinceJun1 = 0
        foreach ($pr in $prs) {
            if ($pr.createdAt) { try { if ([datetime]$pr.createdAt -gt $jun1) { $sinceJun1++ } } catch {} }
        }

        $rate = if ($daysSinceJun1 -gt 0) { [math]::Round($sinceJun1 / $daysSinceJun1, 1) } else { 0 }
        $span = if ($dates.Count -ge 2) { ($dates[-1] - $dates[0]).TotalDays } else { 0 }
        $last = if ($dates.Count -gt 0) { $dates[-1] } else { $null }
        $idle = if ($last) { [math]::Round(($now - $last).TotalDays, 1) } else { 999 }
        $credited = $closedCount + $mergedCount

        $stats[$a] = @{ credited = $credited; open = $openCount; total = $prs.Count; rate = $rate; idle = $idle; span = $span }
    }

    $topLogins = $stats.GetEnumerator() |
        Where-Object { $_.Key -ne $Author } |
        Sort-Object { $_.Value.credited } -Descending |
        Select-Object -First $LeaderboardTop |
        ForEach-Object { $_.Key }
    $authors = @($Author) + $topLogins | Select-Object -Unique

    # Use my classified count for this repo instead of raw closed
    $myRepoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count
    $myRepoIndirect = @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $myRepoCredited = $myRepoShipped + $myRepoIndirect
    if ($stats.ContainsKey($Author)) {
        $stats[$Author].credited = $myRepoCredited
        if ($stats[$Author].span -gt 0) {
            $stats[$Author].rate = [math]::Round($stats[$Author].total / $stats[$Author].span, 1)
        }
    }

    $sorted = $stats.GetEnumerator() | Sort-Object { $_.Value.credited } -Descending
    $myRank = 1
    foreach ($entry in $sorted) { if ($entry.Key -eq $Author) { break }; $myRank++ }

    $leaderboardRows = ""
    $totalContributors = $sorted.Count
    $rank = 1
    foreach ($entry in $sorted) {
        $s = $entry.Value
        $name = $entry.Key
        $isMe = $name -eq $Author
        $statusLabel = if ($s.idle -lt 1) { "Active" } elseif ($s.idle -lt 3) { "Recent" } elseif ($s.idle -lt 7) { "Slowing" } elseif ($s.idle -lt 14) { "Quiet" } else { "Gone" }
        $statusClass = if ($s.idle -lt 3) { "green" } elseif ($s.idle -lt 7) { "yellow" } else { "dim" }
        $rowClass = if ($isMe) { " class=`"is-self`"" } else { "" }
        $nameDisplay = if ($isMe) { "$name" } else { $name }
        $leaderboardRows += "  <tr$rowClass><td>#$rank</td><td><a href=`"https://github.com/$name`">$nameDisplay</a></td><td>$($s.credited)</td><td>$($s.open)</td><td>$($s.rate)/d</td><td><span class=`"$statusClass`">$statusLabel</span></td></tr>`n"
        if ($rank -eq 15 -and $totalContributors -gt 15) {
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
<summary>Projections (you @ $myRate/day, rank #$myRank)</summary>
<table>
  <tr><th>Contributor</th><th>Credited</th><th>Rate</th><th>Catch-up</th></tr>
$projRows</table>
</details>
"@
    } elseif ($myRank -eq 1) {
        $projectionsHtml = "<p class=`"note projections-note`">Rank #1 at $myRate/day</p>"
    }

    $collapsedClass = if ($totalContributors -gt 15) { " collapsed" } else { "" }
    $overlayHtml = if ($totalContributors -gt 15) { "<div class=`"overlay-row`" onclick=`"toggleCollapsedTable('lb-$repoShort')`">Collapse <span class=`"caret`">&#9650;</span></div>`n" } else { "" }
    $isAgent = $repoShort -eq "hermes-agent"
    if ($isAgent) {
        $leaderboardHtml += @"
<details>
<summary><h2>$repoShort Leaderboard</h2></summary>
<div class="collapsible-table leaderboard$collapsedClass" id="lb-$repoShort">
$overlayHtml<table>
  <thead><tr><th>Rank</th><th>Contributor</th><th>Credited</th><th>Open</th><th>Rate</th><th>Status</th></tr></thead>
  <tbody>
$leaderboardRows  </tbody>
</table>
</div>
$projectionsHtml
</details>

"@
    } else {
        $leaderboardHtml += @"
<h2>$repoShort Leaderboard</h2>
<div class="collapsible-table leaderboard$collapsedClass" id="lb-$repoShort">
$overlayHtml<table>
  <thead><tr><th>Rank</th><th>Contributor</th><th>Credited</th><th>Open</th><th>Rate</th><th>Status</th></tr></thead>
  <tbody>
$leaderboardRows  </tbody>
</table>
</div>
$projectionsHtml

"@
    }
}

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

$shippedItems = @()
foreach ($pr in $shipped) {
    $repoLabel = if ($pr.repo -match "hermes-agent") { "agent" } else { "webui" }
    $shippedItems += [pscustomobject]@{
        pr = $pr
        repoLabel = $repoLabel
        statusLabel = "Shipped"
        statusClass = "tag-shipped"
        releaseLabel = if ($pr.release) { $pr.release } else { "" }
        sortDate = if ($pr.closedAt) { [datetime]$pr.closedAt } elseif ($pr.createdAt) { [datetime]$pr.createdAt } else { [datetime]::MinValue }
        displayDate = if ($pr.closedAt) { Format-EasternDate $pr.closedAt } else { Format-EasternDate $pr.createdAt }
    }
}
foreach ($pr in $acceptedIndirect) {
    $repoLabel = if ($pr.repo -match "hermes-agent") { "agent" } else { "webui" }
    $shippedItems += [pscustomobject]@{
        pr = $pr
        repoLabel = $repoLabel
        statusLabel = "Indirect"
        statusClass = "tag-accepted"
        releaseLabel = "indirect"
        sortDate = if ($pr.closedAt) { [datetime]$pr.closedAt } elseif ($pr.createdAt) { [datetime]$pr.createdAt } else { [datetime]::MinValue }
        displayDate = if ($pr.closedAt) { Format-EasternDate $pr.closedAt } else { Format-EasternDate $pr.createdAt }
    }
}
$shippedItems = @($shippedItems | Sort-Object sortDate -Descending)

$shippedRows = ""
$shippedCount = $shippedItems.Count
$shipIndex = 1
foreach ($item in $shippedItems) {
    $pr = $item.pr
    $shippedRows += "  <tr><td><a href=`"https://github.com/$($pr.repo)/pull/$($pr.number)`">#$($pr.number)</a></td><td>$($item.repoLabel)</td><td><span class=`"tag $($item.statusClass)`">$($item.statusLabel)</span></td><td>$($item.displayDate)</td><td>$($item.releaseLabel)</td></tr>`n"
    if ($shipIndex -eq 15 -and $shippedCount -gt 15) {
        $shippedRows += "  <tr class=`"expand-row`" onclick=`"toggleCollapsedTable('shipped-prs')`"><td colspan=`"5`">Show all $shippedCount shipped PRs <span class=`"caret`">&#9660;</span></td></tr>`n"
    }
    $shipIndex++
}

$shippedCollapsedClass = if ($shippedCount -gt 15) { " collapsed" } else { "" }
$shippedOverlayHtml = if ($shippedCount -gt 15) { "<div class=`"overlay-row`" onclick=`"toggleCollapsedTable('shipped-prs')`">Collapse <span class=`"caret`">&#9650;</span></div>`n" } else { "" }

$repoSections = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    $repoPRs = @($allPRs | Where-Object { $_.repo -eq $repo })
    $repoOpen = @($repoPRs | Where-Object { $_.state -eq "OPEN" }).Count
    $repoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count
    $repoAccepted = @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $repoLost = @($lost | Where-Object { $_.repo -eq $repo }).Count
    $repoWithdrawn = @($withdrawn | Where-Object { $_.repo -eq $repo }).Count
    $repoRejected = @($rejected | Where-Object { $_.repo -eq $repo }).Count

    $repoSections += @"
<h2>$repoShort ($($repoPRs.Count) PRs)</h2>
<table>
  <tr><th>Status</th><th>Count</th><th>Details</th></tr>
  <tr><td><span class="tag tag-shipped">Shipped</span></td><td>$($repoShipped + $repoAccepted)</td><td>$repoShipped confirmed shipped$(if ($repoAccepted -gt 0) { ", $repoAccepted accepted indirectly" })</td></tr>
  <tr><td><span class="tag tag-open">Open</span></td><td>$repoOpen</td><td>Awaiting maintainer review</td></tr>
$(if ($repoLost -gt 0) { "  <tr><td><span class=`"tag tag-rejected`">Lost</span></td><td>$repoLost</td><td>Competing PR won</td></tr>`n" })$(if ($repoWithdrawn -gt 0) { "  <tr><td><span class=`"tag tag-withdrawn`">Withdrawn</span></td><td>$repoWithdrawn</td><td>Closed without maintainer action</td></tr>`n" })</table>

"@
}

$dateStr = $now.ToString("MMMM d, yyyy")

# Calculate time span from earliest to latest PR
$allDates = @()
foreach ($pr in $allPRs) {
    if ($pr.createdAt) { try { $allDates += [datetime]$pr.createdAt } catch {} }
}
$allDates = @($allDates | Sort-Object)
if ($allDates.Count -ge 2) {
    $spanDays = [math]::Ceiling(($allDates[-1] - $allDates[0]).TotalDays)
    $timeSpan = "$spanDays days"
    $timeRange = "$($allDates[0].ToString('MMMM d'))-$($allDates[-1].ToString('MMMM d, yyyy'))"
} else {
    $timeSpan = "N/A"
    $timeRange = ""
}

$barShipped = [math]::Round(($shipped.Count / $allPRs.Count) * 100, 1)
$barAccepted = [math]::Round(($acceptedIndirect.Count / $allPRs.Count) * 100, 1)
$barLost = [math]::Round(($lost.Count / $allPRs.Count) * 100, 1)
$barWithdrawn = [math]::Round(($withdrawn.Count / $allPRs.Count) * 100, 1)
$barOpen = [math]::Round(($open.Count / $allPRs.Count) * 100, 1)

$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>pr-sweeps Stats</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="darkreader-lock" />
<meta name="color-scheme" content="light dark" />
<link rel="stylesheet" href="../style.css">
</head>
<body class="pr">

<div class="top-row">
  <h1><a class="back-link" href="../"><svg viewBox="0 0 16 16" width="1em" height="1em"><path d="M10 2L4 8l6 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>pr-sweeps Stats</h1>
  <nav class="nav-links">
    <a href="../pr-targets/">Targets</a>
    <span class="nav-sep">/</span>
    <span class="current">Stats</span>
    <span class="nav-sep">/</span>
    <a href="https://github.com/rodboev/pr-sweep">pr-sweep</a> <span class="private">(private)</span>
  </nav>
</div>
<p class="subtitle">$Author contributions to nesquena/hermes-webui + NousResearch/hermes-agent</p>

<div class="grid">
  <div class="stat-card"><div class="number">$($allPRs.Count)</div><div class="label">Total PRs</div></div>
  <div class="stat-card"><div class="number green">$totalAccepted</div><div class="label">Shipped</div></div>
  <div class="stat-card"><div class="number yellow">$($open.Count)</div><div class="label">Open</div></div>
  <div class="stat-card"><div class="number">$($lost.Count)</div><div class="label">Lost</div></div>
</div>
<div class="grid">
  <div class="stat-card"><div class="number green">${acceptanceRate}%</div><div class="label">Acceptance rate on resolved PRs ($totalAccepted shipped, $($lost.Count) lost out of $totalResolved resolved)</div></div>
  <div class="stat-card"><div class="number blue">$timeSpan</div><div class="label">Time span ($timeRange)</div></div>
</div>

<h2>Breakdown</h2>

<div class="bar-container">
  <div class="bar-segment bar-shipped" data-width="${barShipped}"$(if ($barShipped -gt 4) { " title=`"$($shipped.Count)`"" })>$(if ($barShipped -gt 4) { $shipped.Count })</div>
  <div class="bar-segment bar-accepted" data-width="${barAccepted}">$(if ($barAccepted -gt 4) { $acceptedIndirect.Count })</div>
  <div class="bar-segment bar-lost" data-width="${barLost}">$(if ($barLost -gt 4) { $lost.Count })</div>
  <div class="bar-segment bar-withdrawn" data-width="${barWithdrawn}">$(if ($barWithdrawn -gt 4) { $withdrawn.Count })</div>
  <div class="bar-segment bar-open" data-width="${barOpen}" title="$($open.Count)">$($open.Count)</div>
</div>
<div class="legend">
  <div class="legend-item"><div class="legend-dot legend-dot-shipped"></div> Shipped ($($shipped.Count))</div>
$(if ($acceptedIndirect.Count -gt 0) { "  <div class=`"legend-item`"><div class=`"legend-dot legend-dot-accepted`"></div> Accepted indirectly ($($acceptedIndirect.Count))</div>`n" })  <div class="legend-item"><div class="legend-dot legend-dot-lost"></div> Lost ($($lost.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-withdrawn"></div> Withdrawn ($($withdrawn.Count))</div>
  <div class="legend-item"><div class="legend-dot legend-dot-open"></div> Open ($($open.Count))</div>
</div>

$repoSections

$leaderboardHtml

$representativeHtml

<h2>Shipped PRs ($totalAccepted)</h2>
<div class="collapsible-table shipped-prs$shippedCollapsedClass" id="shipped-prs">
$shippedOverlayHtml<table>
  <thead><tr><th>PR</th><th>Repo</th><th>Status</th><th>ET</th><th>Release</th></tr></thead>
  <tbody>
$shippedRows  </tbody>
</table>
</div>

<h2>Methodology</h2>
<div class="section">
  <p>Both repos use a cherry-pick workflow: the maintainer picks commits and closes the PR without GitHub's merge button, so <code>mergedAt</code> is always null. "Shipped" is determined from maintainer comments referencing a release version.</p>
  <p>PRs classified as "withdrawn" had no maintainer interaction beyond automated bot reviews (Greptile). "Lost" means a competing PR addressing the same issue was accepted instead (superseded or consolidated by another contributor's PR).</p>
  <p>"Credited" in the leaderboard counts closed + merged PRs. For $Author, this is refined to shipped only (comment-based classification). Other contributors use raw closed count as a proxy since comment scanning at scale would hit API rate limits.</p>
</div>

<p class="footer">Generated $dateStr from GitHub API. Source: <a href="https://github.com/$ReadmeRepo">$ReadmeRepo</a></p>

<script>
function setBarWidths() {
  document.querySelectorAll('.bar-segment[data-width]').forEach(function(segment) {
    segment.style.width = segment.getAttribute('data-width') + '%';
  });
}
function updateCollapsedOverlays() {
  document.querySelectorAll('.collapsible-table:not(.collapsed)').forEach(function(block) {
    var tbody = block.querySelector('tbody');
    if (!tbody) return;
    var firstRow = tbody.querySelector('tr:not(.expand-row)');
    if (!firstRow) return;
    var overlay = block.querySelector('.overlay-row');
    if (!overlay) return;
    var rect = firstRow.getBoundingClientRect();
    var headerTh = block.querySelector('thead th');
    var headerBottom = headerTh ? headerTh.getBoundingClientRect().bottom : 0;
    if (rect.bottom < headerBottom) {
      overlay.classList.add('visible');
    } else {
      overlay.classList.remove('visible');
    }
  });
}
function toggleCollapsedTable(id) {
  var el = document.getElementById(id);
  var wasExpanded = !el.classList.contains('collapsed');
  el.classList.toggle('collapsed');
  if (wasExpanded) el.scrollIntoView({ block: 'start', behavior: 'auto' });
  updateCollapsedOverlays();
}
function toggleLeaderboard(id) {
  toggleCollapsedTable(id);
}
setBarWidths();
updateCollapsedOverlays();
document.addEventListener('scroll', updateCollapsedOverlays, { passive: true });
</script>

</body>
</html>
"@

$html | Out-File -FilePath $OutFile -Encoding utf8
Write-Host "`nWritten to $OutFile" -ForegroundColor Green
Write-Host "  Total: $($allPRs.Count) | Shipped: $totalAccepted | Open: $($open.Count) | Lost: $($lost.Count) | Rate: ${acceptanceRate}%"

if ($OpenOutput) {
    Start-Process $OutFile
}
