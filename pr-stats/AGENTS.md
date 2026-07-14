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

## Conventions

PR pipeline skills (sweep, find, code, review, rework, cleanup) live in `C:\Apps\hermes\.claude\skills\pr\`.

Cache refreshes are scoped: reclassify affected `repo#number` entries through `live_evidence` + `classify_closed_pr`; never rebuild the full cache (`--classify-cache`, 4+ hours) without explicit approval.
