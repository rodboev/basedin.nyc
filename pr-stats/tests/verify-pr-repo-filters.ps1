param(
    [string]$Path = "$PSScriptRoot\..\index.html"
)

$content = Get-Content -Raw -Path $Path

if ($content -notmatch 'id="pr-repo-pills"') {
    throw "Could not find repo filter pills container."
}

if ($content -notmatch 'data-repo="all">All</div>' -or
    $content -notmatch 'data-repo="webui">webui</div>' -or
    $content -notmatch 'data-repo="agent">agent</div>' -or
    $content -notmatch 'data-repo="claude-mem">claude-mem</div>') {
    throw "Repo filter pills are missing expected All/webui/agent/claude-mem options."
}

if ($content -match 'data-repo="[^"]+">[^<]*\(\d+\)</div>') {
    throw "Repo filter pills should not include counts."
}

if ($content -notmatch 'var CURRENT_PR_FILTER = \{\s*statusKey: ''shipped'',\s*repoKey: ''all''\s*\};') {
    throw "Default PR filter state is not shipped + all repos."
}

if ($content -notmatch 'data-status="open">Open \(\d+\)</div>' -or
    $content -notmatch 'data-status="shipped">Shipped \(\d+\)</div>' -or
    $content -notmatch 'data-status="not-shipped">Not Shipped \(\d+\)</div>') {
    throw "Status filter pills are missing expected Open/Shipped/Not Shipped options with counts."
}

if ($content -notmatch 'function updatePrFilterPills') {
    throw "Client-side pill count updater is missing."
}

if ($content -notmatch 'updatePrFilterPills\(\);') {
    throw "Pill counts are not refreshed on load or filter changes."
}

if ($content -notmatch 'renderPrTable\(CURRENT_PR_FILTER\.statusKey, CURRENT_PR_FILTER\.repoKey\);') {
    throw "PR table does not render using combined status and repo filters."
}

if ($content -notmatch 'document\.getElementById\(''pr-repo-pills''\)\.addEventListener') {
    throw "Repo filter click handler is missing."
}

if ($content -notmatch 'statusKey === ''not-shipped''') {
    throw "Combined not-shipped filtering logic is missing."
}

Write-Host "PR repo filters are present and default to all repos."
