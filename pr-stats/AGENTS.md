# PR Stats

Public contribution stats page for Rod Boev. Hosted at `basedin.nyc/pr-stats/`.

## Architecture

`generate.py` is the single entry point. It fetches PR data from the GitHub API via the `gh` CLI, classifies closed PRs by reading maintainer comments and timelines (`core/classify.py`), builds per-repo community leaderboards with catch-up projections, and renders `index.html` from the Jinja2 shell in `template.html`. Does not open the output file.

Supporting files:
- `repos.txt`: active repo list, one `owner/repo` per line; `#` comments deactivate entries.
- `core/`: typed modules. `classify.py` owns the classification ladder and every pattern; `credit.py` is the hermes-webui release-credit pipeline (CHANGELOG, Co-authored-by trailers, absorb-commit scan, ship-comment classifier); `leaderboard.py`, `report.py`, `page.py`, `timeline.py`, `cache.py`, `github.py`, `classification_rebuild.py`.
- `.pr-classification-cache.json`: persistent cache of PR state, classifications, leaderboard stats, and release-credit metadata. Keyed by `repo#number` for PRs and `repo:leaderboard:*` for boards.
- `template.html`: Jinja2 shell with nine slots; `render_report_page` validates the slot set in both directions.
- `index.html`: generated output. Hand-edits are allowed for layout/style; backport to `template.html`/`core/page.py` only when explicitly asked.
- `tests/`: pytest suite (offline by default; `-m live` selects live verifications such as `test_classification_parity_live.py`).

Run `python -m pytest -q` and `C:\Apps\Python313\Scripts\mypy.exe --strict core generate.py` before calling work done.

## Classification (closed PRs for Rod's repos)

- **shipped**: maintainer-accepted (merge, cherry-pick, release credit, salvage)
- **accepted-indirect**: content landed through another PR with credit (rolls up under shipped)
- **superseded**: maintainer (or the author) replaced it with a different implementation
- **lost**: a competing contributor's PR was accepted instead
- **withdrawn**: author pullback or no maintainer interaction; detected but excluded from reported totals and acceptance rate

Ship-comment detection is maintainer-gated (`has_maintainer_ship_comment`); a supersession names its replacement PR, and a third-party replacement classifies as lost (`get_superseded_evidence`).

## Leaderboards

Community boards rank third-party contributors only; owners, maintainers, bots, and automated accounts are excluded (see `is_leaderboard_bot`, `configured_repo_leaderboard_exclusions`, `is_leaderboard_excluded_login` in `core/leaderboard.py`). hermes-webui uses a 5-source credit count via `core/credit.py`. Other repos use evidence-based shipped classification for top contributors and merged-PR totals as a proxy below that.

## Conventions

PR pipeline skills (sweep, find, code, review, rework, cleanup) live in `C:\Apps\hermes\.claude\skills\pr\`.

Cache refreshes are scoped: reclassify affected `repo#number` entries through `live_evidence` + `classify_closed_pr`; never rebuild the full cache (`--classify-cache`, 4+ hours) without explicit approval.
