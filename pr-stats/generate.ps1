param(
    [string]$Author = "rodboev",
    [string[]]$Repos = @("nesquena/hermes-webui", "NousResearch/hermes-agent"),
    [string]$ReadmeRepo = "rodboev/pr-sweep",
    [string]$OutFile = "$PSScriptRoot\index.html",
    [int]$LeaderboardTop = 10
)

$shippedPatterns = @("Shipped", "shipped", "cherry-picked", "merged-via", "Salvaged into", "salvaged into")
$acceptedPatterns = @("Superseded by", "superseded by", "consolidated", "Consolidating")
$duplicatePatterns = @("Duplicate", "duplicate")

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

$shipped = @(); $acceptedIndirect = @(); $duplicates = @(); $withdrawn = @(); $rejected = @()

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

    if ($isShipped) {
        $pr.classification = "shipped"
        $shipped += $pr
        Write-Host " shipped" -ForegroundColor Green
    } elseif ($isAccepted) {
        $pr.classification = "accepted-indirect"
        $acceptedIndirect += $pr
        Write-Host " accepted (indirect)" -ForegroundColor Cyan
    } elseif ($isDuplicate) {
        $pr.classification = "duplicate"
        $duplicates += $pr
        Write-Host " duplicate" -ForegroundColor Blue
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
$totalResolved = $closed.Count
$acceptanceRate = if ($rejected.Count -eq 0 -and $totalResolved -gt 0) { "100" } elseif ($totalResolved -gt 0) { [math]::Round(($totalAccepted / $totalResolved) * 100) } else { "N/A" }

# Build per-repo leaderboards
Write-Host "`nBuilding leaderboards..." -ForegroundColor DarkGray
$now = Get-Date
$jun1 = [datetime]"2026-06-01"
$daysSinceJun1 = ($now - $jun1).TotalDays

$leaderboardHtml = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "  $repoShort contributors..." -ForegroundColor DarkGray

    $raw = gh pr list --repo $repo --state all --limit 500 --json author,number,createdAt,state 2>$null
    if (-not $raw) { continue }
    $repoPRs = @(($raw | ConvertFrom-Json) | ForEach-Object { $_ })

    $byAuthor = @{}
    foreach ($pr in $repoPRs) {
        $login = $pr.author.login
        if (-not $login -or $login -eq "nesquena-hermes") { continue }
        if (-not $byAuthor.ContainsKey($login)) { $byAuthor[$login] = @() }
        $byAuthor[$login] += $pr
    }

    $topLogins = $byAuthor.GetEnumerator() |
        Where-Object { $_.Key -ne $Author } |
        Sort-Object { @($_.Value | Where-Object { $_.state -eq "CLOSED" -or $_.state -eq "MERGED" }).Count } -Descending |
        Select-Object -First $LeaderboardTop |
        ForEach-Object { $_.Key }
    $authors = @($Author) + $topLogins | Select-Object -Unique

    $stats = @{}
    foreach ($a in $authors) {
        $prs = if ($byAuthor.ContainsKey($a)) { $byAuthor[$a] } else { @() }

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
    $rank = 1
    foreach ($entry in $sorted) {
        $s = $entry.Value
        $name = $entry.Key
        $isMe = $name -eq $Author
        $statusLabel = if ($s.idle -lt 1) { "Active" } elseif ($s.idle -lt 3) { "Recent" } elseif ($s.idle -lt 7) { "Slowing" } elseif ($s.idle -lt 14) { "Quiet" } else { "Gone" }
        $statusClass = if ($s.idle -lt 3) { "green" } elseif ($s.idle -lt 7) { "yellow" } else { "dim" }
        $rowClass = if ($isMe) { " style=`"font-weight:600; background:rgba(63,185,80,0.06)`"" } else { "" }
        $nameDisplay = if ($isMe) { "$name" } else { $name }
        $leaderboardRows += "  <tr$rowClass><td>#$rank</td><td><a href=`"https://github.com/$name`">$nameDisplay</a></td><td>$($s.credited)</td><td>$($s.open)</td><td>$($s.rate)/d</td><td><span class=`"$statusClass`">$statusLabel</span></td></tr>`n"
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
<details style="margin-top:0.75rem">
<summary style="cursor:pointer; font-size:0.85rem; color:var(--dim)">Projections (you @ $myRate/day, rank #$myRank)</summary>
<table style="margin-top:0.5rem">
  <tr><th>Contributor</th><th>Credited</th><th>Rate</th><th>Catch-up</th></tr>
$projRows</table>
</details>
"@
    } elseif ($myRank -eq 1) {
        $projectionsHtml = "<p style=`"margin-top:0.75rem; font-size:0.85rem; color:var(--dim)`">Rank #1 at $myRate/day</p>"
    }

    $leaderboardHtml += @"
<h2>$repoShort Leaderboard</h2>
<table>
  <tr><th>Rank</th><th>Contributor</th><th>Credited</th><th>Open</th><th>Rate</th><th>Status</th></tr>
$leaderboardRows</table>
$projectionsHtml

"@
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

$shippedRows = ""
foreach ($pr in ($shipped | Sort-Object { $_.release })) {
    $repoLabel = if ($pr.repo -match "hermes-agent") { "agent" } else { "webui" }
    $rel = if ($pr.release) { $pr.release } else { "" }
    $shippedRows += "  <tr><td><a href=`"https://github.com/$($pr.repo)/pull/$($pr.number)`">#$($pr.number)</a></td><td>$repoLabel</td><td>$rel</td></tr>`n"
}
foreach ($pr in $acceptedIndirect) {
    $repoLabel = if ($pr.repo -match "hermes-agent") { "agent" } else { "webui" }
    $shippedRows += "  <tr><td><a href=`"https://github.com/$($pr.repo)/pull/$($pr.number)`">#$($pr.number)</a></td><td>$repoLabel</td><td>indirect</td></tr>`n"
}

$repoSections = ""
foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    $repoPRs = @($allPRs | Where-Object { $_.repo -eq $repo })
    $repoOpen = @($repoPRs | Where-Object { $_.state -eq "OPEN" }).Count
    $repoShipped = @($shipped | Where-Object { $_.repo -eq $repo }).Count
    $repoAccepted = @($acceptedIndirect | Where-Object { $_.repo -eq $repo }).Count
    $repoDups = @($duplicates | Where-Object { $_.repo -eq $repo }).Count
    $repoWithdrawn = @($withdrawn | Where-Object { $_.repo -eq $repo }).Count
    $repoRejected = @($rejected | Where-Object { $_.repo -eq $repo }).Count

    $repoSections += @"
<h2>$repoShort ($($repoPRs.Count) PRs)</h2>
<table>
  <tr><th>Status</th><th>Count</th><th>Details</th></tr>
  <tr><td><span class="tag tag-shipped">Shipped</span></td><td>$($repoShipped + $repoAccepted)</td><td>$repoShipped confirmed shipped$(if ($repoAccepted -gt 0) { ", $repoAccepted accepted indirectly" })</td></tr>
  <tr><td><span class="tag tag-open">Open</span></td><td>$repoOpen</td><td>Awaiting maintainer review</td></tr>
$(if ($repoDups -gt 0) { "  <tr><td><span class=`"tag tag-dup`">Duplicate</span></td><td>$repoDups</td><td>Consolidated into other PRs</td></tr>`n" })$(if ($repoWithdrawn -gt 0) { "  <tr><td><span class=`"tag tag-withdrawn`">Withdrawn</span></td><td>$repoWithdrawn</td><td>Closed without maintainer action</td></tr>`n" })  <tr><td><span class="tag tag-rejected">Rejected</span></td><td>$repoRejected</td><td>$(if ($repoRejected -eq 0) { 'None' })</td></tr>
</table>

"@
}

$dateStr = $now.ToString("MMMM d, yyyy")

$barShipped = [math]::Round(($shipped.Count / $allPRs.Count) * 100, 1)
$barAccepted = [math]::Round(($acceptedIndirect.Count / $allPRs.Count) * 100, 1)
$barDup = [math]::Round(($duplicates.Count / $allPRs.Count) * 100, 1)
$barWithdrawn = [math]::Round(($withdrawn.Count / $allPRs.Count) * 100, 1)
$barOpen = [math]::Round(($open.Count / $allPRs.Count) * 100, 1)

$html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PR Pipeline Stats</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="darkreader-lock" />
<meta name="color-scheme" content="light dark" />
<style>
  :root { --bg: #fff; --card: #fff; --border: rgba(0,0,0,0.08); --text: #1a1a1a; --dim: #888; --link: #3376d2; --green: #1a7f37; --red: #cf222e; --yellow: #9a6700; --blue: #3376d2; --purple: #8250df; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161616; --card: #1e1e1e; --border: rgba(255,255,255,0.08); --text: rgba(255,255,255,0.88); --dim: rgba(255,255,255,0.45); --link: #6ba3e8; --green: #3fb950; --red: #f85149; --yellow: #d29922; --blue: #58a6ff; --purple: #bc8cff; }
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html { background: var(--bg); color-scheme: light dark; }
  html[data-darkreader-mode] { filter: none !important; background: var(--bg) !important; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; color: var(--text); max-width: 960px; width: calc(100% - 10vw); margin: min(2vw, 2rem) auto; padding-bottom: 3rem; line-height: 1.55; -webkit-font-smoothing: antialiased; }
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }
  h1 { font-size: 1.75rem; font-weight: 600; margin-bottom: 0.15rem; }
  .subtitle { color: var(--dim); font-size: 0.9rem; margin-bottom: 2rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.75rem; margin-bottom: 1.5rem; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.1rem 1.25rem; }
  .stat-card .number { font-size: 2.25rem; font-weight: 700; line-height: 1.1; }
  .stat-card .label { color: var(--dim); font-size: 0.8rem; margin-top: 0.3rem; }
  .green { color: var(--green); }
  .yellow { color: var(--yellow); }
  .blue { color: var(--blue); }
  .purple { color: var(--purple); }
  .red { color: var(--red); }
  .dim { color: var(--dim); font-size: 0.8em; }
  h2 { font-size: 1.1rem; font-weight: 600; margin: 2rem 0 0.75rem; padding-bottom: 0.4rem; border-bottom: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 1.25rem; }
  th { text-align: left; padding: 0.55rem 0.9rem; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--dim); border-bottom: 1px solid var(--border); }
  td { padding: 0.45rem 0.9rem; border-top: 1px solid var(--border); font-size: 0.85rem; }
  tr:first-child td { border-top: none; }
  .tag { display: inline-block; padding: 0.1rem 0.45rem; border-radius: 10px; font-size: 0.7rem; font-weight: 600; }
  .tag-shipped { background: rgba(63,185,80,0.15); color: var(--green); }
  .tag-open { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .tag-dup { background: rgba(88,166,255,0.15); color: var(--blue); }
  .tag-withdrawn { background: rgba(128,128,128,0.15); color: var(--dim); }
  .tag-rejected { background: rgba(248,81,73,0.15); color: var(--red); }
  .bar-container { display: flex; height: 22px; border-radius: 6px; overflow: hidden; margin: 0.5rem 0; }
  .bar-segment { display: flex; align-items: center; justify-content: center; font-size: 0.65rem; font-weight: 600; color: #fff; min-width: 2px; }
  .bar-shipped { background: var(--green); }
  .bar-accepted { background: var(--purple); }
  .bar-dup { background: var(--blue); }
  .bar-withdrawn { background: #666; }
  .bar-open { background: var(--yellow); }
  .legend { display: flex; gap: 0.9rem; flex-wrap: wrap; margin-top: 0.4rem; font-size: 0.75rem; color: var(--dim); }
  .legend-item { display: flex; align-items: center; gap: 0.3rem; }
  .legend-dot { width: 9px; height: 9px; border-radius: 50%; }
  .note { color: var(--dim); font-size: 0.8rem; margin-top: 0.5rem; }
  .section { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.1rem; margin-bottom: 1.25rem; }
  .section p + p { margin-top: 0.4rem; }
  details summary { cursor: pointer; font-size: 0.85rem; color: var(--dim); }
  .footer { text-align: center; color: var(--dim); font-size: 0.75rem; margin-top: 2.5rem; }
  @media (max-width: 600px) {
    .grid { grid-template-columns: repeat(2, 1fr); }
    body { width: calc(100% - 2rem); }
  }
</style>
</head>
<body>

<h1>PR Pipeline Stats</h1>
<p class="subtitle">$Author contributions to NousResearch/hermes-agent + nesquena/hermes-webui</p>

<div class="grid">
  <div class="stat-card"><div class="number">$($allPRs.Count)</div><div class="label">Total PRs</div></div>
  <div class="stat-card"><div class="number green">$totalAccepted</div><div class="label">Shipped</div></div>
  <div class="stat-card"><div class="number yellow">$($open.Count)</div><div class="label">Open</div></div>
  <div class="stat-card"><div class="number">$($rejected.Count)</div><div class="label red">Rejected</div></div>
</div>
<div class="grid">
  <div class="stat-card"><div class="number green">${acceptanceRate}%</div><div class="label">Acceptance rate on resolved PRs ($totalAccepted accepted, $($rejected.Count) rejected out of $totalResolved closed)</div></div>
</div>

<h2>Breakdown</h2>

<div class="bar-container">
  <div class="bar-segment bar-shipped" style="width:${barShipped}%"$(if ($barShipped -gt 4) { " title=`"$($shipped.Count)`"" })>$(if ($barShipped -gt 4) { $shipped.Count })</div>
  <div class="bar-segment bar-accepted" style="width:${barAccepted}%"></div>
  <div class="bar-segment bar-dup" style="width:${barDup}%"></div>
  <div class="bar-segment bar-withdrawn" style="width:${barWithdrawn}%"></div>
  <div class="bar-segment bar-open" style="width:${barOpen}%" title="$($open.Count)">$($open.Count)</div>
</div>
<div class="legend">
  <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div> Shipped ($($shipped.Count))</div>
  <div class="legend-item"><div class="legend-dot" style="background:var(--purple)"></div> Accepted indirectly ($($acceptedIndirect.Count))</div>
  <div class="legend-item"><div class="legend-dot" style="background:var(--blue)"></div> Duplicate ($($duplicates.Count))</div>
  <div class="legend-item"><div class="legend-dot" style="background:#666"></div> Withdrawn ($($withdrawn.Count))</div>
  <div class="legend-item"><div class="legend-dot" style="background:var(--yellow)"></div> Open ($($open.Count))</div>
</div>

$repoSections

$leaderboardHtml

$representativeHtml

<h2>Shipped PRs ($totalAccepted)</h2>
<table>
  <tr><th>PR</th><th>Repo</th><th>Release</th></tr>
$shippedRows</table>

<h2>Methodology</h2>
<div class="section">
  <p>Both repos use a cherry-pick workflow: the maintainer picks commits and closes the PR without GitHub's merge button, so <code>mergedAt</code> is always null. "Shipped" is determined from maintainer comments referencing a release version.</p>
  <p>PRs classified as "withdrawn" had no maintainer interaction beyond automated bot reviews (Greptile). "Accepted indirectly" means the fix was cherry-picked into a follow-up PR or consolidated with a duplicate.</p>
  <p>"Credited" in the leaderboard counts closed + merged PRs. For $Author, this is refined to shipped + accepted indirectly (comment-based classification). Other contributors use raw closed count as a proxy since comment scanning at scale would hit API rate limits.</p>
</div>

<p class="footer">Generated $dateStr from GitHub API. Source: <a href="https://github.com/$ReadmeRepo">$ReadmeRepo</a></p>

</body>
</html>
"@

$html | Out-File -FilePath $OutFile -Encoding utf8
Write-Host "`nWritten to $OutFile" -ForegroundColor Green
Write-Host "  Total: $($allPRs.Count) | Shipped: $totalAccepted | Open: $($open.Count) | Rejected: $($rejected.Count) | Rate: ${acceptanceRate}%"

Start-Process $OutFile
