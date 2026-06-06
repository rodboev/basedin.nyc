param(
    [string[]]$Repos = @("nesquena/hermes-webui", "NousResearch/hermes-agent"),
    [string]$Me = "rodboev",
    [int]$Top = 11,
    [switch]$Projections = $true,
    [double]$MyRate = 0,
    [string[]]$Targets = @(),
    [string[]]$At = @(),
    [switch]$NoRestart
)

$shippedPatterns = @("Shipped", "shipped", "cherry-picked", "merged-via", "Salvaged into", "salvaged into")
$acceptedPatterns = @("Superseded by", "superseded by", "consolidated", "Consolidating")
$duplicatePatterns = @("Duplicate", "duplicate")

$now = Get-Date
$jun1 = [datetime]"2026-06-01"
$daysSinceJun1 = ($now - $jun1).TotalDays

foreach ($repo in $Repos) {
    $repoShort = ($repo -split '/')[-1]
    Write-Host "`n  === $repoShort ===" -ForegroundColor Cyan
    Write-Host "  Discovering contributors..." -ForegroundColor DarkGray

    $raw = gh pr list --repo $repo --state all --limit 500 --json author,number,createdAt,state 2>$null
    if (-not $raw) { Write-Host "  No data for $repo" -ForegroundColor Red; continue }
    $allRepoPRs = @(($raw | ConvertFrom-Json) | ForEach-Object { $_ })

    # Group by author
    $byAuthor = @{}
    foreach ($pr in $allRepoPRs) {
        $login = $pr.author.login
        if (-not $login -or $login -eq "nesquena-hermes") { continue }
        if (-not $byAuthor.ContainsKey($login)) { $byAuthor[$login] = @() }
        $byAuthor[$login] += $pr
    }

    # Pick top contributors + always include me
    $topLogins = $byAuthor.GetEnumerator() |
        Where-Object { $_.Key -ne $Me } |
        Sort-Object { $_.Value.Count } -Descending |
        Select-Object -First $Top |
        ForEach-Object { $_.Key }
    $authors = @($Me) + $topLogins | Select-Object -Unique

    # Build stats per author
    $stats = @{}
    foreach ($a in $authors) {
        $prs = if ($byAuthor.ContainsKey($a)) { $byAuthor[$a] } else { @() }

        $dates = @()
        foreach ($pr in $prs) {
            if ($pr.createdAt) { try { $dates += [datetime]$pr.createdAt } catch {} }
        }
        $dates = @($dates | Sort-Object)

        $closed = @($prs | Where-Object { $_.state -eq "CLOSED" }).Count
        $open = @($prs | Where-Object { $_.state -eq "OPEN" }).Count
        $merged = @($prs | Where-Object { $_.state -eq "MERGED" }).Count

        $sinceJun1 = 0
        foreach ($pr in $prs) {
            if ($pr.createdAt) { try { if ([datetime]$pr.createdAt -gt $jun1) { $sinceJun1++ } } catch {} }
        }

        $rate = if ($daysSinceJun1 -gt 0) { [math]::Round($sinceJun1 / $daysSinceJun1, 1) } else { 0 }
        $span = if ($dates.Count -ge 2) { ($dates[-1] - $dates[0]).TotalDays } else { 0 }
        $last = if ($dates.Count -gt 0) { $dates[-1] } else { $null }
        $idle = if ($last) { [math]::Round(($now - $last).TotalDays, 1) } else { 999 }
        $credited = $closed + $merged
        $status = if ($idle -lt 1) { "Active" } elseif ($idle -lt 3) { "Recent" } elseif ($idle -lt 7) { "Slowing" } elseif ($idle -lt 14) { "Quiet" } else { "Gone" }

        $stats[$a] = @{
            credited = $credited; open = $open; total = $prs.Count; rate = $rate
            last = $last; idle = $idle; status = $status; span = $span
        }
    }

    # Classify my closed PRs in this repo
    $myShipped = 0; $myIndirect = 0; $myDuplicate = 0; $myWithdrawn = 0
    $myClosedPRs = @($prs | Where-Object { $_.state -eq "CLOSED" })
    if (-not $byAuthor.ContainsKey($Me)) { $myClosedPRs = @() }
    else { $myClosedPRs = @($byAuthor[$Me] | Where-Object { $_.state -eq "CLOSED" }) }

    if ($myClosedPRs.Count -gt 0) {
        Write-Host "  Classifying $($myClosedPRs.Count) closed PRs..." -ForegroundColor DarkGray
        foreach ($pr in $myClosedPRs) {
            $detail = gh pr view $pr.number --repo $repo --json comments 2>$null | ConvertFrom-Json
            $comments = ($detail.comments | Where-Object { $_.author.login -ne "greptile-apps" } | ForEach-Object { $_.body }) -join "`n---`n"

            $isShipped = $false
            foreach ($p in $shippedPatterns) { if ($comments -match [regex]::Escape($p)) { $isShipped = $true; break } }
            $isAccepted = $false
            foreach ($p in $acceptedPatterns) { if ($comments -match [regex]::Escape($p)) { $isAccepted = $true; break } }
            $isDuplicate = $false
            foreach ($p in $duplicatePatterns) { if ($comments -match [regex]::Escape($p)) { $isDuplicate = $true; break } }

            if ($isShipped) { $myShipped++ }
            elseif ($isAccepted) { $myIndirect++ }
            elseif ($isDuplicate) { $myDuplicate++ }
            else { $myWithdrawn++ }
        }
    }

    $myAccepted = $myShipped + $myIndirect
    if ($stats.ContainsKey($Me)) { $stats[$Me].credited = $myAccepted }

    if ($stats.ContainsKey($Me) -and $stats[$Me].span -gt 0) {
        $stats[$Me].rate = [math]::Round($stats[$Me].total / $stats[$Me].span, 1)
    }

    $sorted = $stats.GetEnumerator() | Sort-Object { $_.Value.credited } -Descending
    $rank = 1

    Write-Host ""
    Write-Host ("{0,4} {1,-18} {2,8} {3,7} {4,8} {5,8} {6,-8}" -f "Rank","Contributor","Credited","Pending","Rate/d","Idle(d)","Status") -ForegroundColor Cyan
    Write-Host ("{0,4} {1,-18} {2,8} {3,7} {4,8} {5,8} {6,-8}" -f "----","------------------","--------","-------","------","-------","------")

    foreach ($entry in $sorted) {
        $s = $entry.Value
        $name = $entry.Key
        $marker = if ($name -eq $Me) { " <--" } else { "" }
        $color = if ($name -eq $Me) { "Green" } else { "White" }
        Write-Host ("{0,4} {1,-18} {2,8} {3,7} {4,8} {5,8} {6,-8}{7}" -f "#$rank", $name, $s.credited, $s.open, "$($s.rate)/d", $s.idle, $s.status, $marker) -ForegroundColor $color
        $rank++
    }

    $myCredited = if ($stats.ContainsKey($Me)) { $stats[$Me].credited } else { 0 }
    $myPending = if ($stats.ContainsKey($Me)) { $stats[$Me].open } else { 0 }
    $effectiveRate = if ($MyRate -gt 0) { $MyRate } elseif ($stats.ContainsKey($Me)) { $stats[$Me].rate } else { 0 }

    Write-Host ""
    Write-Host "  You: $myShipped shipped + $myIndirect indirect + $myDuplicate dup + $myWithdrawn withdrawn = $($myShipped + $myIndirect + $myDuplicate + $myWithdrawn) closed" -ForegroundColor Green
    Write-Host "  Credited (shipped + indirect): $myCredited | Open: $myPending | Total: $($myCredited + $myPending + $myDuplicate + $myWithdrawn) | Rate: $effectiveRate PRs/day" -ForegroundColor Green

    if ($Projections -or $Targets.Count -gt 0 -or $At.Count -gt 0) {
        $repoTargets = $Targets
        if ($repoTargets.Count -eq 0 -and ($Projections -or $At.Count -gt 0)) {
            $repoTargets = @($sorted | Where-Object { $_.Value.credited -gt $myCredited } | ForEach-Object { $_.Key })
        }

        if ($repoTargets.Count -gt 0) {
            Write-Host ""
            Write-Host "  PROJECTIONS (you @ $effectiveRate/day)" -ForegroundColor Yellow
            Write-Host ""

            foreach ($t in $repoTargets) {
                if (-not $stats.ContainsKey($t)) { continue }
                $ts = $stats[$t]
                $theirRate = $ts.rate
                $gap = $ts.credited - $myCredited
                $netRate = $effectiveRate - $theirRate
                if ($netRate -le 0) {
                    Write-Host ("  vs {0}: they're at {1} (+{2} ahead), gaining at {3}/d -- you won't catch them at current rates" -f $t, $ts.credited, $gap, $theirRate) -ForegroundColor Red
                } else {
                    $days = [math]::Round($gap / $netRate, 1)
                    $when = $now.AddDays($days)
                    Write-Host ("  vs {0}: {1} credited (+{2} ahead, {3}/d) -- you pass them in {4}d ({5})" -f $t, $ts.credited, $gap, $theirRate, $days, $when.ToString("ddd MMM d h:mm tt")) -ForegroundColor White
                }
            }
        }

        if ($At.Count -gt 0) {
            Write-Host ""
            Write-Host "  RANK AT SPECIFIC TIMES" -ForegroundColor Yellow
            Write-Host ""
            foreach ($dt in $At) {
                $target = [datetime]$dt
                $daysAway = ($target - $now).TotalDays
                if ($daysAway -lt 0) { Write-Host "  $dt is in the past" -ForegroundColor Red; continue }
                $myCount = $myCredited + [math]::Round($effectiveRate * $daysAway)
                $board = @()
                foreach ($entry in $sorted) {
                    $s = $entry.Value
                    $projected = $s.credited + [math]::Round($s.rate * $daysAway)
                    if ($entry.Key -ne $Me) { $board += @{ name = $entry.Key; count = $projected } }
                }
                $above = @($board | Where-Object { $_.count -gt $myCount }).Count
                $projRank = $above + 1
                Write-Host ("  {0}: ~{1} credited, rank #{2}" -f $target.ToString("ddd MMM d h:mm tt"), $myCount, $projRank)
            }
        }
    }
}

if (-not $Projections -and $Targets.Count -eq 0 -and $At.Count -eq 0) {
    Write-Host ""
    Write-Host "  Tip: use -Projections for forward estimates, -Targets user1,user2 for specific races," -ForegroundColor DarkGray
    Write-Host "       -At '2026-06-07 09:00','2026-06-09 23:59' for rank at specific times," -ForegroundColor DarkGray
    Write-Host "       -MyRate 20 to override your projected rate." -ForegroundColor DarkGray
}

if (-not $NoRestart) {
    Read-Host "Press any key to continue . . ."
}

Write-Host ""
