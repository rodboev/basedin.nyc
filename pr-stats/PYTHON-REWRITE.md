# pr-stats: PowerShell → Python rewrite

This document is a briefing for refactoring `generate.ps1` and `credit-lib.ps1` into typed Python with tests. The execution prompt for a fresh session is `rewrite-prompt.md`; start there when kicking off the actual work.

**Source of truth:** the ps1 files and `tests/` are the authority for behavior. Sections below that describe specific logic (the classification ladder, line numbers, function names) are snapshots and may lag the code. When this document and the code disagree, the code wins; port what the code does, then note the divergence here.

## What exists today

Two PowerShell files totaling ~4,400 lines produce `index.html`, a public GitHub contribution stats page at `basedin.nyc/pr-stats/`. A third file, `generate-timeline.py`, already runs as a Python post-processor (injects a Chart.js progress chart into the generated HTML). The output is a standalone HTML page with embedded JS for filtering, collapsible tables, and leaderboard projections. Client-side chart animation lives in `timeline.js` (~1,010 lines), loaded via a `<script>` tag that generate-timeline.py injects.

### generate.ps1 (~3,160 lines)

Entry point. Does five things:

1. **Fetches PR data** from GitHub via `gh` CLI for a configurable list of repos and a single author.
2. **Classifies closed PRs** into shipped/superseded/lost/withdrawn by inspecting timeline events, cross-references, maintainer comments, release tags, and credited merge evidence. This is the core logic, about 150 lines of branching conditions in `Get-ClosedPullRequestClassification`, fed by a growing set of evidence predicates (positive-reference-context checks, credited-merged-sibling detection, maintainer carry-forward via commit-author logins).
3. **Builds community leaderboards** per repo, ranking third-party contributors by credited PRs. Uses batched GraphQL for counts, with caching, TTL profiles, and incremental refresh.
4. **Emits index.html** with stat cards, status bars, per-repo breakdowns, representative PRs, filterable PR table, and leaderboard sections.
5. **Persists a classification cache** (`.pr-classification-cache.json`) with versioned schemas, TTL-based expiry, and multiple sub-caches (PR states, leaderboard stats, credit maps, commit scan metadata).

### credit-lib.ps1 (~1,250 lines)

Dot-sourced by generate.ps1. Handles hermes-webui's six-source release-credit pipeline:
- CHANGELOG.md parsing (multiple attribution formats)
- Co-authored-by trailer extraction from git commits
- Filtered merged PR scanning via GraphQL pagination
- "Absorbed" credit detection (closed PRs whose numbers appear in release sections)
- Ship-comment classification (a priority-ladder classifier for maintainer comments)

All six sources are merged, verified against PR authorship, and deduplicated into per-login credit counts.

### generate-timeline.py (~285 lines)

Post-processor. Reads the classification cache, fetches LOC data via `gh`, builds daily/cumulative chart data, and injects Chart.js sections into index.html. Already Python with proper typing patterns. This file stays as-is or gets absorbed into the main pipeline. Note that it currently parses `.pr-classification-cache.json` directly; after absorption it must route through the shared cache module, not keep its own parser.

### timeline.js (~1,010 lines)

Client-side Chart.js animation and state transitions for the injected timeline chart: trendline regression, label transitions, `lerpFading`, and a large `transitionRange` state machine. **This file is out of scope for the Python rewrite.** It runs in the browser; changing the generator language does not touch it. The lerp duplication was already collapsed into a single `lerpFading` helper with two call sites. If it gets attention, that is step 10 in the migration path, a separate bounded pass.

### Tests (pr-stats/tests/)

13 PowerShell verification scripts plus two leaderboard-retry helpers. Each verify script is a standalone `.ps1` that dot-sources generate.ps1, exercises specific classification scenarios, and exits with a pass/fail code. No test framework; just manual assertions. These need to become pytest tests, and each one encodes a real regression that must survive the port.

## Why this outgrew PowerShell

The cache serialization alone (Import/Export-ClassificationCache) is ~420 lines of manual PSObject property iteration, rebuilding hashtables by hand. In Python, this is a Pydantic model with `json.loads()`.

`Invoke-Gh` manually creates a ProcessStartInfo, reads stdout/stderr async to avoid pipe deadlocks, implements timeouts, works around PowerShell's comma-splitting bug for `--json` args, and now wraps the whole thing in a retry loop with exponential backoff (rate limit, 5xx, network error, timeout each classified by regex over stderr). Python's `subprocess.run(capture_output=True, timeout=120)` replaces the process ceremony in one call, but the retry classification is policy and must be ported as-is: same retry reasons, same backoff shape (base 5s, cap 300s, rate limits floor at 60s), non-retryable failures return stdout without retry.

The `Add-Member` pattern for attaching properties to PSCustomObjects is a workaround for PS1's lack of lightweight record types. Python has dataclasses and TypedDicts.

No type safety across 4,100 lines means a typo in a property name silently produces `None`. Python with type hints and mypy catches these statically.

## Architecture for the Python version

### Module structure

```
pr-stats/
├── generate.py              (entry point, CLI args, orchestration)
├── core/
│   ├── __init__.py
│   ├── models.py             (dataclasses/Pydantic for PR, Classification, LeaderboardEntry, CacheSchema)
│   ├── github.py             (gh CLI wrapper, GraphQL batching, REST calls)
│   ├── classify.py           (closed PR classification state machine)
│   ├── leaderboard.py        (community leaderboard building, caching, refresh)
│   ├── credit.py             (credit-lib equivalent: changelog, commits, absorb, ship-comments)
│   ├── cache.py              (cache load/save/TTL logic using Pydantic for schema)
│   ├── html.py               (HTML generation, template rendering)
│   └── timeline.py           (absorb generate-timeline.py logic)
├── tests/
│   ├── conftest.py           (shared fixtures, mock PR factories)
│   ├── test_classify.py      (classification logic unit tests)
│   ├── test_credit.py        (credit pipeline tests)
│   ├── test_leaderboard.py   (leaderboard ranking/exclusion tests)
│   ├── test_cache.py         (cache round-trip, TTL expiry, version migration)
│   └── test_html.py          (output structure smoke tests)
├── pyproject.toml            (project config, dependencies, mypy/pytest config)
└── PYTHON-REWRITE.md         (this file)
```

### Key design decisions

**Pydantic for the cache schema.** The cache JSON has 12+ top-level keys, nested maps of maps, and version-gated migration. Define it as Pydantic models. `model_validate_json()` replaces 250 lines of Import-ClassificationCache. `model_dump_json()` replaces 170 lines of Export-ClassificationCache.

**dataclasses for internal records.** PR data, classification results, leaderboard entries, credit pairs. Use `@dataclass(frozen=True)` where mutation isn't needed.

**subprocess for gh CLI.** Don't introduce PyGitHub or similar; the existing code's contract with `gh` is well-tested and `gh` handles auth. Wrap it in a thin `run_gh(*args, timeout=120) -> str` function that handles errors and returns stdout.

**Jinja2 for HTML.** The current script builds HTML through 250+ lines of string interpolation in PowerShell here-strings. Move the HTML template to a Jinja2 file. Pass the data structures in. Escape user content properly (the PS1 version doesn't).

**Type hints everywhere, enforced by mypy (strict mode).** This is the primary motivation for the rewrite. Every function gets annotated parameters and return types.

**pytest with fixtures.** Build `make_pr()` and `make_evidence()` factory fixtures that create test objects with sensible defaults. Test the classification logic with one scenario per ladder branch plus one per evidence predicate boundary (positive vs negative reference context, carry-forward with and without commit-author match). Test credit map merging and deduplication. Test cache TTL expiry and corruption tolerance with real file I/O. The full proof obligations (parity gates, contract-isolation rows, negative-bypass checks) are specified in `rewrite-prompt.md`.

### Classification logic mapping

Snapshot as of 2026-07-01. The function is `Get-ClosedPullRequestClassification` (currently lines 1993-2149); reread it before porting, this area changes most often. The priority order:

1. Direct merge or timeline-shipped (release closer, cross-ref, release commit) → `shipped`
2. Author withdrew (author close comment, no maintainer interaction) → `withdrawn`
3. Accepted sibling (credited merged PR references this one) → `accepted-indirect`
4. Credited ship (maintainer comment with ship evidence + credit patterns) → `accepted-indirect`
5. Maintainer superseded → `superseded`
6. Duplicate → `lost`
7. Non-maintainer superseded reference → `lost`
8. Comment-based shipped evidence → `shipped`
9. No comments at all → `withdrawn`
10. Default → `lost`

The ladder shape is stable but the evidence predicates feeding it gained nuance in late June 2026:

- Release closers and release cross-refs only count as shipped when `Test-IsPositiveReleaseReferenceToPullRequest` passes: the release PR's text must reference this PR in a positive context (ship/credit/co-author/fix vocabulary or an @-mention of the author) and not a negative one (superseded, alternative, in favor of). This kills "closed by release that actually replaced it" false positives.
- Accepted-sibling detection is two-stage: `Get-TimelineCreditedMergedPullRequest` first, then `Get-ReferencedMergedPullRequest` over comment text. A merged sibling counts as credited if its reference text has a positive context for this PR, or via maintainer carry-forward: a maintainer comment crediting the merged PR plus the original author appearing in the merged PR's commit-author logins (`Get-PullRequestCommitAuthorLogins`).
- `Get-PullRequestState` gained `-RequireBody` and persists PR body text; the cache gained `prPullStates` (with body) and `prAuthorsByNumber` sub-caches.

Each branch sets `classification`, `evidence_kind`, `via_label`, `via_url`, and `release`. The Python version should return a `ClassificationResult` dataclass with these fields. Port the predicates as named functions with the same decision boundaries; every regex pattern set ($shippedPatterns, $supersededPatterns, $creditPatterns, $continuationPatterns, positive/negative context patterns) lives in classify.py and nowhere else.

### Credit pipeline mapping (credit-lib.ps1)

Snapshot as of 2026-07-01; reread credit-lib.ps1 before porting. Six credit sources for hermes-webui, merged and verified:

| Source | PS1 function | Description |
|--------|-------------|-------------|
| a | `Get-WebuiChangelogCreditMap` | Parse CHANGELOG.md for `PR #N by @user` patterns (6 regex variants) |
| b | `Get-WebuiCommitCreditMap` | Co-authored-by trailers in commits that reference PR numbers |
| c | `Get-WebuiFilteredMergedPrCreditMap` | GraphQL scan of all merged PRs, filtering out maintainers/bots/vehicles |
| d | `Get-WebuiShipCommentCreditMap` | Priority-ladder comment classifier (own-ship > co-author-ship > plain-ship > deflection) |
| e | `Get-WebuiAbsorbCommitCreditMap` | Closed PRs whose numbers appear in commit subjects (non-merge, no co-author) |
| f | `Get-WebuiReleaseAbsorbedCreditMap` | Closed PRs referenced in CHANGELOG release sections without attribution |

All six are merged via `Merge-CreditMaps`, then verified by `Confirm-UpstreamReleaseCreditMap` which checks authorship ownership and filters out maintainer release vehicles.

### What to preserve exactly

- The classification cache format (`.pr-classification-cache.json`). Existing caches must load without a full rebuild. If the Pydantic schema can't parse an old field, fail gracefully and rebuild that sub-cache. The schema now includes `prPullStates` (with body text) and `prAuthorsByNumber`; enumerate the actual top-level keys from the live cache file when writing the models, don't trust this list.
- The `Invoke-Gh` retry policy: retry-reason classification (rate limit, 5xx, network, timeout), exponential backoff with the same constants, no retry on other failures.
- The HTML output structure. The page uses `../style.css` and `../assets/script.js` from the parent site. CSS classes, data attributes, and element IDs are referenced by external JS. Don't rename them.
- The `$Repos` parameter list and `$RepoLeaderboardConfig` config. Move these to a YAML or TOML config file, or keep them as Python dicts at the top of generate.py.
- Console progress output with colors. Use `rich` or plain ANSI escape codes.
- The sanity checks (`Test-GeneratedReportSane`) that refuse to overwrite the existing index.html if the report looks broken.

### What can change

- The `Invoke-Gh` process-spawning ceremony → `subprocess.run()`.
- The PSObject/hashtable ceremony → dataclasses/Pydantic.
- Manual JSON serialization → Pydantic's `model_dump_json()`.
- String-interpolated HTML → Jinja2 templates.
- Standalone test scripts → pytest test functions.
- The `generate-timeline.py` post-processing step can be absorbed into the main pipeline as an optional `--timeline` flag.

### Dependencies

```
pydantic>=2.0
jinja2
rich           # colored console output
pytest         # dev dependency
mypy           # dev dependency
```

Do not add `PyGithub`, `httpx`, or other GitHub libraries. Keep `gh` CLI as the API interface.

### Running

```bash
python generate.py                          # default: all repos, all span
python generate.py --repos nesquena/hermes-webui --span default
python generate.py --rebuild-cache
python generate.py --verify-webui-credits-only
pytest tests/                                # run all tests
mypy core/                               # type check
```

### Migration path

1. Build the Python version alongside the PS1 files. Don't delete generate.ps1 until the Python version produces identical HTML output.
2. Start with `models.py` and `cache.py` (Pydantic schemas, load/save). Verify round-trip against the existing cache file.
3. Port `classify.py` next with full test coverage before anything else.
4. Port `credit.py` (credit-lib equivalent).
5. Port `leaderboard.py` and `github.py`.
6. Port `html.py` last (Jinja2 templates).
7. Wire up `generate.py` as the entry point. Compare output to PS1 version.
8. Absorb `generate-timeline.py` into the pipeline, routing cache reads through cache.py instead of its own parser.
9. Delete the PS1 files.
10. Optional, separate pass: decompose `transitionRange` in timeline.js. It is roughly 475 lines, half the file, and the only remaining large untyped surface once the Python side lands. The lerp duplication is already resolved; this step is about the state machine, not the lerps. Skip it if the chart behavior is stable and nobody is editing that file.
