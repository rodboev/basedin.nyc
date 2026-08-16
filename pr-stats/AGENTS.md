# PR Stats

Public contribution stats page for Rod Boev. Hosted at `basedin.nyc/pr-stats/`.

## Architecture

`generate.py` is the single entry point. It fetches PR data from the GitHub API via the `gh` CLI, classifies closed PRs by reading maintainer comments and timelines (`core/classify.py`), builds per-repo community leaderboards with catch-up projections, and renders `index.html` from the Jinja2 shell in `template.html`. Does not open the output file.

Supporting files:
- `repos.txt`: active repo list, one `owner/repo` per line; `#` comments deactivate entries. Entries may name a repo by a pre-transfer owner (`microsoft/presidio`); see Repo names below.
- `core/`: typed modules. `classify.py` owns the classification ladder and every pattern; `credit.py` is the hermes-webui release-credit pipeline (CHANGELOG, Co-authored-by trailers, absorb-commit scan, ship-comment classifier); `repos.py` resolves repos.txt entries to canonical names and holds the display map; `leaderboard.py`, `report.py`, `page.py`, `timeline.py`, `cache.py`, `github.py`, `classification_rebuild.py`.
- `.pr-classification-cache.json`: persistent cache of PR state, classifications, leaderboard stats, and release-credit metadata. Keyed by `repo#number` for PRs and `repo:leaderboard:*` for boards.
- `template.html`: Jinja2 shell with nine slots; `render_report_page` validates the slot set in both directions.
- `index.html`: generated output. Hand-edits are allowed for layout/style; backport to `template.html`/`core/page.py` only when explicitly asked.
- `tests/`: pytest suite (offline by default; `-m live` selects live verifications such as `test_classification_parity_live.py`).

Run `python -m pytest -q` and `C:\Apps\Python313\Scripts\mypy.exe --strict core generate.py` before calling work done.

## Repo names

`generate_report` resolves every repos.txt entry through `resolve_canonical_repos` (`gh api repos/<slug> --jq .full_name`) before any fetching, because GitHub's two APIs disagree about transfers: `gh api repos/...` and GraphQL `repository()` follow the redirect, but the search API's `repo:` qualifier does not and silently matches nothing. A transferred repo therefore built a full leaderboard while reporting zero author PRs (presidio, moved from `microsoft` to `data-privacy-stack`, 2026-07-14). An entry GitHub cannot resolve is a hard error, and two entries resolving to one repo is a hard error; a transient `gh` failure falls back to the name as written.

Everything behind the page keys on the canonical name: cache keys, leaderboard entries, release data, classification. The page keys on the name as written in repos.txt, since the leaderboard trades on name recognition and the old owner is often the recognizable one. `core/repos.py` maps canonical names back to their as-written display names through `display_repo`, used by `render_repo_link`. Rename a repos.txt entry and its cache entries orphan; that is the intended cost of making repos.txt the display source.

## Classification (closed PRs for Rod's repos)

- **shipped**: maintainer-accepted (merge, cherry-pick, release credit, salvage)
- **accepted-indirect**: content landed through another PR with credit (rolls up under shipped)
- **superseded**: maintainer (or the author) replaced it with a different implementation
- **lost**: a competing contributor's PR was accepted instead
- **withdrawn**: author pullback or no maintainer interaction; detected but excluded from reported totals and acceptance rate

Ship-comment detection is maintainer-gated (`has_maintainer_ship_comment`); a supersession names its replacement PR, and a third-party replacement classifies as lost (`get_superseded_evidence`).

## Leaderboards

Community boards rank third-party contributors only; owners, maintainers, bots (any `[bot]` suffix or `app/` prefix), and automated accounts are excluded (`is_leaderboard_bot`, `is_leaderboard_excluded_login` in `core/leaderboard.py`). Maintainer lists are not hardcoded: `repo_leaderboard_config` parses the `- Members:` block from the pr-sweep overlay at `D:\Repos\.claude\pr-sweep\repos\<repo-short>\config.md` (override with `--overlay-config-dir`). The same member list feeds classification through `live_evidence`, so a missing config.md silently drops both the leaderboard exclusions and the config-listed maintainer gating (authorAssociation gating still applies); verify the overlay bundle exists before activating a repo in `repos.txt`. Integration bots with classification privileges (orca's `buf0-bot[bot]`) are not humans, never appear in Members blocks, and stay hardcoded in `REPO_INTEGRATION_BOTS`. Overlay bundles key on the lowercased repo short name, so two active repos must not share one.

The board pipeline is split into a write side and a read side, both in `core/leaderboard.py`:

- **Write**: `fetch_community_leaderboard` (called per active repo from `generate_report`, shielded against `GhRetryExhausted`) refreshes the cache entry `repo|community-shipped-v4|all` via paginated GraphQL over `repository.pullRequests`, 100/page, newest-first. It updates only the keys it owns (`cachedAt`, `refreshedAt`, `scanPages`, `logins`, `stats`, `shippedCounts`) and preserves everything else in the entry, notably `releaseCreditCounts`, which only the retired PS1 ever wrote. `shippedCounts` takes the max of the prior (possibly evidence-based) count and the fresh merged-PR proxy per login; logins absent from the fresh scan (deleted accounts) keep their prior counts. An empty community is a complete scan and still stamps the TTL.
- **Read**: `cached_leaderboard_rows` (via `core/page.py`) renders whatever entry exists. The credit profile decides the credited column: hermes-webui (`changelog-release`) takes the per-login max of `releaseCreditCounts` and `shippedCounts`; all other repos (`github-evidence`) read `shippedCounts`. hermes-webui's 5-source credit pipeline in `core/credit.py` serves the verify CLI paths; it does not populate the board entry.

### Scan budget, TTL, and staleness

`LEADERBOARD_PAGES_PER_RUN` (700) is a per-run chunk, not a limit. A scan that runs out of budget or loses a page banks its cursor and per-login tallies under the entry's `scan` key and resumes there next run, so progress is never thrown away and a repo can exceed one run's budget without failing. The partial state lives beside the rendered keys rather than replacing them, so a board keeps serving its last complete scan while the next one builds. `LEADERBOARD_MAX_TOTAL_PAGES` (10,000) is only a runaway-cursor backstop; reaching it drops the scan state and reports `failed` rather than retrying forever. The chunk is deliberately larger than the biggest active repo (hermes-agent, 651 pages) because a multi-run build stretches the effective refresh period by however many runs it takes, the TTL clock starting at completion.

TTL scales with the work the last scan cost: `leaderboard_ttl_seconds` is a 24h floor plus 15 minutes per page, capped at 72h, with the page count read from the entry's own `scanPages`. SkillSpector's 3 pages refresh daily, hermes-webui's 53 every ~37h, mastra's 158 every ~64h, hermes-agent's 651 every 72h. Boards that predate `scanPages` read 0 and get the 24h floor, which self-corrects on the first refresh.

`fetch_community_leaderboard` returns a `LeaderboardRefresh` (`fresh`, `refreshed`, `partial`, `failed`), not a bool. The distinction is the point: the old bool collapsed "inside its TTL, nothing to do" and "every attempt aborted" into the same `False`, which is how hermes-agent's board stayed frozen from 2026-06-24 to 2026-08-16 without a single warning. `warn_stale_leaderboards` is the backstop for that whole class, checking every active repo's board age against `LEADERBOARD_STALE_FACTOR` × its TTL at render time regardless of why it is old.
The curated map is a floor, never a gate. It reads `releaseCreditCounts` first because that map counts absorbed and co-authored work the merged-PR proxy cannot see, but a login missing from it falls through to the merged count instead of rendering 0. Gating on it dropped every contributor who landed work after the map froze (2026-07-02) below all 218 credited logins and out of the 50-row cutoff; webtecnica, 14 merged PRs and 4 in upstream's own `CONTRIBUTORS.md`, ranked 219th at 0 credited.

History: the PowerShell rewrite ported only the read side; boards kept rendering from PS1-era cache entries until a repo without one (unsloth) exposed the gap. The write side was added 2026-07-03, initially with `gh pr list --limit 1000` (truncated repos with more PRs and clobbered the credit keys); both were fixed the same day. Three unwired refresh helpers from the abandoned first port were removed too. The 200-page cap that replaced the `--limit` then silently locked out hermes-agent, 65,079 PRs against a 20,000-PR ceiling, for the seven weeks until 2026-08-16; the resumable scan, the work-scaled TTL, and the typed refresh outcome all came out of that.

Known gaps (2026-07-03, revised 2026-08-15):

- `releaseCreditCounts` still has no Python writer, so its share of hermes-webui's credited column is frozen at PS1-era values. The merged-count fallback keeps new contributors on the board, but absorbed and co-authored credit earned since the freeze is invisible until `core/credit.py` output is wired into the board entry.
- Evidence-based shipped counts are preserved, not recomputed; a login's evidence count only grows again if their merged-PR proxy overtakes it.

## Breakdown seed

The Breakdown cards, bar, and legend in `index.html` render the **first frame of the load animation**, not the all-time totals. `animateOnLoad` in `timeline.js` walks `BD_LOAD_RANGES` (`[1, 7, 14, 30, 0]`), lerping between real range states from `bdDisplay(bdStats(r))`, and `updateBreakdown(0)` writes the all-time numbers when it settles. `breakdown_seed` in `core/timeline.py` is a port of `bdStats`/`bdDisplay` at `BD_LOAD_RANGES[0]` and must produce byte-identical values, or the page visibly jumps the moment the animation takes the first frame. `test_breakdown_cards_render_the_load_seed_not_the_all_time_rollup` pins the generated markup to the port; the port itself is only pinned by the unit tests, so change one side and you must change the other.

Two rules that look arbitrary are load-bearing. The seed window is 2 days, and both shorter options are traps: a 0-day window is today alone, whose PRs are all still open, giving a 0/0 rate and a 100%-open bar; a 1-day window is the only window in the data that closed nothing but shipped work, giving a 0-denominator 100% that falls to 84% as soon as a second day is in scope, and making the seed the only frame in the run to render a 4-character rate, since `formatAcceptanceRate` switches to decimals above 99. At 2 days the rate lerp is a monotonic 84 -> 97, every value an integer. And bar widths are emitted inline by `render_bar_segments`, never applied from a `data-width` attribute by a script: the segments are flex items with no intrinsic width, so a deferred width leaves the bar collapsed to its labels for the whole first paint.

Phase length is proportional to how far the bar travels, not to the clock, with a floor on the settle phase; and the tick walks the phases along one global `ease` rather than easing each phase. Both exist because the shipped-edge is the thing the eye tracks: equal slices swung it 4.7x between phases, and a per-phase cubic ran it to a standstill at every boundary. The pills therefore stay lit for unequal durations, which is intended. The counts want the opposite schedule (14d->30d covers the most PRs but the least bar) and can't be paced for at the same time; they blur either way, so the bar wins.

Everything `renderBdFrame` does not touch snaps when `updateBreakdown` settles, which is why `bd-rate-label` and `bd-days-label` are lerped there too rather than left as static text. `bdStats` returns `firstDate`/`lastDate` for that, and drops today from both the day tally and the range end exactly as `updateBreakdown` does (`lastDate = prevDate`); `breakdown_seed` mirrors it. That shared rule is what retired `report_activity_summary`, whose own day-count rule rendered 46 against the 47 JS settles on.

The animation previously scaled the all-time totals by a fraction (`dispAt(f)`) instead of reading the ranges, which fabricated outcome counts and acceptance rates that never existed (16 shipped, 0 lost, 14%) and bottomed out at a 0/0 rate that a hardcoded `barSh = 66.7` was covering for. Don't reintroduce a scaled seed.

## Timeline LOC attribution

The daily and cumulative LOC series, and the Avg net LOC/day card, count **merged** code and charge it to the day it merged, not the day the PR opened. `aggregate_daily` in `core/timeline.py` buckets net additions minus deletions into the resolved date (`mergedAt` wins) and only for shipped PRs; open PRs contribute nothing until they land, and lost/superseded PRs never do. The daily `loc`, `additions`, `deletions`, `files`, `locPerPr`, `filesPerPr`, and `cumLoc` all come from that shipped bucket, so a day's bar and its tooltip agree. Avg LOC/day divides by days with shipped code (`prsShipped > 0`), a separate tally from the PR active-day count that Avg PRs/day and the range label use; `bdStats`/`updateBreakdown` in `timeline.js` keep both, and `breakdown_seed` must too.

## Conventions

PR pipeline skills (sweep, find, code, review, rework, cleanup) live in `C:\Apps\hermes\.claude\skills\pr\`.

Cache refreshes are scoped: reclassify affected `repo#number` entries through `live_evidence` + `classify_closed_pr`; never rebuild the full cache (`--classify-cache`, 4+ hours) without explicit approval.
