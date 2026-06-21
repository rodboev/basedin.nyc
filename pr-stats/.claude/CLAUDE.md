# PR Stats

Public contribution stats page for Rod Boev. Hosted at `basedin.nyc/pr-stats/`.

## Architecture

`generate.ps1` is the single entry point. It fetches PR data from the GitHub API, classifies closed PRs by reading maintainer comments, builds per-repo community leaderboards with catch-up projections, and emits `index.html`. Does not open the output file.

Supporting files:
- `credit-lib.ps1`: hermes-webui release-credit pipeline (CHANGELOG, Co-authored-by trailers, absorb-commit scan, ship-comment classifier). Dot-sourced by generate.ps1.
- `.pr-classification-cache.json`: persistent cache of PR state, classifications, leaderboard stats, and release-credit metadata. Keyed by `repo/number` for PRs and `repo:leaderboard:*` for boards.
- `index.html`: generated output. Hand-edits are allowed for layout/style; backport to generate.ps1 only when explicitly asked.
- `tests/`: PowerShell verification scripts for classification logic and leaderboard accuracy.

## Classification (closed PRs for Rod's repos)

- **shipped**: maintainer-accepted (merge, cherry-pick, release credit, salvage)
- **superseded**: maintainer replaced with a different implementation
- **lost**: a competing contributor's PR was accepted instead
- **withdrawn**: author pullback or no maintainer interaction; detected but excluded from reported totals and acceptance rate

## Leaderboards

Community boards rank third-party contributors only; owners, maintainers, bots, and automated accounts are excluded (see `Test-IsLeaderboardBot`, `Get-RepoLeaderboardExclusions`, `Test-IsLeaderboardExcludedLogin`). hermes-webui uses a 5-source credit count via credit-lib.ps1. Other repos use evidence-based shipped classification for top contributors and merged-PR totals as a proxy below that.

## Conventions

PR pipeline skills (sweep, find, code, review, rework, cleanup) live in `C:\Apps\hermes\.claude\skills\pr\`.

PowerShell 5.1 array flattening: `@(($raw | ConvertFrom-Json) | ForEach-Object { $_ })` to enumerate, not `@($raw | ConvertFrom-Json)`.
