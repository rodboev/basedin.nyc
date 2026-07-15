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

Everything behind the page keys on the canonical name: cache keys, leaderboard entries, release data, classification. The page keys on the name as written in repos.txt, since the leaderboard trades on name recognition and the old owner is often the recognizable one. `core/repos.py` holds both directions: `display_repo` (canonical to as-written, used by `render_repo_link`) and `canonical_repo` (as-written to canonical, used when parsing repo names out of the representative README). Rename a repos.txt entry and its cache entries orphan; that is the intended cost of making repos.txt the display source.

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

- **Write**: `fetch_community_leaderboard` (called per active repo from `generate_report`, shielded against `GhRetryExhausted`) refreshes the cache entry `repo|community-shipped-v4|all` on a 24h TTL via paginated GraphQL over `repository.pullRequests` (100/page; a failed page or hitting the 200-page cap aborts the refresh and keeps the existing entry; an empty community still stamps the TTL). It updates only the keys it owns (`cachedAt`, `refreshedAt`, `logins`, `stats`, `shippedCounts`) and preserves everything else in the entry, notably `releaseCreditCounts`, which only the retired PS1 ever wrote. `shippedCounts` takes the max of the prior (possibly evidence-based) count and the fresh merged-PR proxy per login; logins absent from the fresh scan (deleted accounts) keep their prior counts.
- **Read**: `cached_leaderboard_rows` (via `core/page.py`) renders whatever entry exists. The credit profile decides the credited column: hermes-webui (`changelog-release`) reads `releaseCreditCounts`; all other repos (`github-evidence`) read `shippedCounts`. hermes-webui's 5-source credit pipeline in `core/credit.py` serves the verify CLI paths; it does not populate the board entry.

History: the PowerShell rewrite ported only the read side; boards kept rendering from PS1-era cache entries until a repo without one (unsloth) exposed the gap. The write side was added 2026-07-03, initially with `gh pr list --limit 1000` (truncated repos with more PRs and clobbered the credit keys); both were fixed the same day. Three unwired refresh helpers from the abandoned first port were removed too.

Known gaps (2026-07-03):

- `releaseCreditCounts` has no Python writer, so hermes-webui's credited column is frozen at PS1-era values; new release credits require wiring `core/credit.py` output into the board entry.
- Evidence-based shipped counts are preserved, not recomputed; a login's evidence count only grows again if their merged-PR proxy overtakes it.

## Breakdown seed

The Breakdown cards, bar, and legend in `index.html` render the **first frame of the load animation**, not the all-time totals. `animateOnLoad` in `timeline.js` walks `BD_LOAD_RANGES` (`[1, 7, 14, 30, 0]`), lerping between real range states from `bdDisplay(bdStats(r))`, and `updateBreakdown(0)` writes the all-time numbers when it settles. `breakdown_seed` in `core/timeline.py` is a port of `bdStats`/`bdDisplay` at `BD_LOAD_RANGES[0]` and must produce byte-identical values, or the page visibly jumps the moment the animation takes the first frame. `test_breakdown_cards_render_the_load_seed_not_the_all_time_rollup` pins the generated markup to the port; the port itself is only pinned by the unit tests, so change one side and you must change the other.

Two rules that look arbitrary are load-bearing. The window is 1 day, not 0: today's PRs are all still open, so a 0-day window puts the acceptance rate at 0/0 and the bar at 100% open. And bar widths are emitted inline by `render_bar_segments`, never applied from a `data-width` attribute by a script: the segments are flex items with no intrinsic width, so a deferred width leaves the bar collapsed to its labels for the whole first paint.

The animation previously scaled the all-time totals by a fraction (`dispAt(f)`) instead of reading the ranges, which fabricated outcome counts and acceptance rates that never existed (16 shipped, 0 lost, 14%) and bottomed out at a 0/0 rate that a hardcoded `barSh = 66.7` was covering for. Don't reintroduce a scaled seed. Note `bd-rate-label` and `bd-days-label` do not animate, so they read the seed window until `updateBreakdown` settles.

Python and JS still disagree on the all-time day count (`report_activity_summary` computes from PR items and excludes today; `bdStats` computes from the daily series and subtracts today from the count). It renders 46 vs the 47 JS settles on. The seed does not go through that path, so this is latent, not visible.

## Conventions

PR pipeline skills (sweep, find, code, review, rework, cleanup) live in `C:\Apps\hermes\.claude\skills\pr\`.

Cache refreshes are scoped: reclassify affected `repo#number` entries through `live_evidence` + `classify_closed_pr`; never rebuild the full cache (`--classify-cache`, 4+ hours) without explicit approval.
