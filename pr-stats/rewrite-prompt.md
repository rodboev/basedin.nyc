# Execution prompt: pr-stats PowerShell → Python rewrite

You are starting a fresh session to rewrite the pr-stats generator from PowerShell to typed Python. Work in `C:\Dropbox\Projects\basedin.nyc\pr-stats\`. Read `PYTHON-REWRITE.md` in full before writing any code; it holds the module structure, design decisions, dependency policy, and migration order. This prompt adds the execution contract: source-of-truth rules, architecture decisions, the required proof matrix, and test discipline.

## Source of truth

`generate.ps1`, `credit-lib.ps1`, and `tests/` are the authority for behavior. PYTHON-REWRITE.md describes intent and architecture; where it summarizes logic (the classification ladder, the credit sources, line numbers) treat those as snapshots and port what the code actually does. Read the current source of each function before porting it. Do not port from memory of the briefing.

## Ground rules

- Build alongside the ps1 files. Nothing gets deleted until the parity gate passes (defined below).
- `.pr-classification-cache.json` is real accumulated data and is expensive to rebuild. Copy it to a scratch location before any experiment that writes to it. Never run with `-RebuildCache` or `-RebuildClassifications`, and never pass the Python equivalents, unless explicitly asked.
- `index.html` is the published page. All parity runs write to scratch output paths (`--out-file`), never the real index.html. The PS1 baseline run uses `-OutFile` the same way.
- Minimize network. The cache file plus recorded gh JSON fixtures cover almost all test needs. Tests must not hit the network; anything that genuinely needs `gh` gets a pytest marker (`@pytest.mark.live`) excluded by default.
- Do not modify `timeline.js`, `../style.css`, or `../assets/script.js`. Do not rename any CSS class, element ID, or data attribute the HTML emits; external JS references them.
- No PyGithub, httpx, requests, or other GitHub libraries. `gh` CLI via subprocess is the only API surface.
- `pwsh` is available for running the PS1 baseline. `gh` is authenticated.
- Commits: single-line messages, imperative, stating why, no body. Commit when a stage's gate passes, not per-file.

## Architecture decisions

The rewrite's central structural rule: policy gets one owner, mechanics can be duplicated. Policy means decision rules (classification boundaries, credit merging semantics, TTL selection, retry classification, sanity gating). Mechanics means shape-shuffling (JSON parsing, subprocess plumbing, string formatting). Three shared authorities exist; every consumer routes through them.

### Classification policy → `core/classify.py`

**Semantic surface:** the decision of what happened to a closed PR (shipped, accepted-indirect, superseded, lost, withdrawn), at the grain of one PR plus its evidence bundle (timeline, comments, sibling PR states).
**Application count:** 3+ consumers: HTML status bars and PR table, leaderboards (contributor shipped counts), timeline chart data. Today the PS1 spreads pattern lists at file top and predicates across the file; generate-timeline.py separately re-reads classification strings from the cache.
**Amplification assessment:** current factor is 2 (generate.ps1 plus generate-timeline.py's independent cache parsing). Target factor 1: classify.py owns the ladder and every predicate; consumers receive `ClassificationResult` values or cache entries produced by it.
**Ownership decision:** shared authority. Interface: `classify_closed_pr(pr: PullRequest, evidence: Evidence) -> ClassificationResult` plus named predicate functions (`is_author_withdrawn`, `has_positive_reference_context`, `is_credited_merged_sibling`, etc.) so tests can pin each boundary. All regex pattern sets (`SHIPPED_PATTERNS`, `SUPERSEDED_PATTERNS`, `CREDIT_PATTERNS`, `CONTINUATION_PATTERNS`, positive/negative context patterns) are module constants here and are imported, never redefined.
**Consumer map:** html.py renders labels from ClassificationResult (routes through owner); leaderboard.py counts by classification value (routes through owner); timeline.py reads classifications via cache.py models (routes through owner); tests use the interface directly.
**Invariants:** for any PR object with optional fields missing, classification returns a valid ClassificationResult without raising. The ladder is strictly ordered; exactly one branch fires. A release reference only proves shipping when its surrounding context is positive.
**Bypass risks:** html.py string-matching status labels or re-deriving state from `mergedAt`; timeline.py keeping its own JSON parsing of classification entries (it does this today in generate-timeline.py and must be migrated); any test asserting against a re-implemented mini-ladder instead of the real one.

### Cache schema and TTL policy → `core/cache.py` + `core/models.py`

**Semantic surface:** what cached knowledge is still trustworthy, at the grain of one sub-cache entry.
**Ownership decision:** shared authority. Pydantic models for the full cache schema; `load_cache(path) -> Cache`, `save_cache(cache, path)`, and TTL functions that reproduce the PS1 TTL profiles (classification TTL varies by classification and evidence kind; leaderboard cache and refresh TTLs are separate). Enumerate the actual top-level keys from the live cache file when writing models; the briefing's list may be incomplete.
**Invariants:** for any byte content of the cache file (truncated, invalid UTF-8, wrong types, unknown version), `load_cache` returns a usable Cache without raising; unparseable sub-caches come back empty and marked for rebuild, parseable ones are preserved. Round-trip of a valid cache is semantically lossless.
**Bypass risks:** timeline.py parsing the JSON file directly; ad-hoc `json.load` anywhere outside cache.py; TTL comparisons re-implemented at a call site.

### gh invocation and retry policy → `core/github.py`

**Semantic surface:** when a failed gh call is worth retrying, at the grain of one subprocess invocation.
**Ownership decision:** shared authority. `run_gh(*args, timeout=120, suppress_errors=False) -> str` wrapping subprocess.run, plus the retry classifier ported exactly: rate limit, 5xx, network error, and timeout retry with exponential backoff (base 5s, cap 300s, rate limit floors at 60s); all other failures return stdout without retry. GraphQL batching helpers build on run_gh.
**Bypass risks:** any module calling subprocess directly; retry logic duplicated at a call site that "really needs" different backoff.

Everything else (leaderboard assembly, credit source scanners, HTML rendering) is a consumer or a mechanics module. Credit merging (`Merge-CreditMaps` + `Confirm-UpstreamReleaseCreditMap` semantics) is policy owned by credit.py, but its six source scanners are mechanics and may share only shape, not rules.

## Required proof matrix

Every row must pass before the corresponding stage is done. Parity rows compare Python output against a PS1 baseline generated in this session from the same cache with network-dependent refresh disabled where possible. Source inspection is never the primary proof for a behavior row.

| # | Surface | Proof | Kind | Expected |
|---|---------|-------|------|----------|
| 1 | Cache round-trip | Load live `.pr-classification-cache.json` (a scratch copy), dump, reload, compare semantically | contract-isolation | Lossless; test runs without network |
| 2 | Cache corruption tolerance | Write pathological bytes to a temp cache file (truncated JSON, invalid UTF-8, deeply nested garbage, valid JSON wrong shape) and load through the real file path, not monkeypatched | negative boundary, real I/O | No exception; affected sub-caches empty; valid sub-caches in a partially bad file preserved |
| 3 | Classification ladder | pytest scenarios via `make_pr()`/`make_evidence()`, one per branch, one per predicate boundary | contract-isolation | Each branch reachable and exclusive; no I/O in these tests |
| 4 | Classification parity | Replay classifier over every PR in the cached corpus; compare classification, evidence_kind, via_label, release against cached PS1 results | behavioral parity | 100% match, or each divergence individually explained and accepted |
| 5 | Existing regression scenarios | Port all 13 `tests/verify-*.ps1` scripts to pytest, preserving each script's specific fixture and assertion | behavioral | All pass; no verify script dropped silently |
| 6 | gh adapter fidelity | Recorded gh JSON fixtures (pr view, GraphQL pages) parse into models with correct field mapping, including absent optional fields | adapter-fidelity | Typed models match recorded shapes; fixtures cite the gh command that produced them |
| 7 | Retry policy | Unit tests feeding stderr samples to the retry classifier; backoff sequence asserted per reason | contract-isolation | Same reasons, delays, and non-retry behavior as PS1 |
| 8 | Credit pipeline parity | Run the six-source webui credit pipeline from cache plus fixtures; compare per-login counts to PS1 `-VerifyWebuiCreditsOnly` output | behavioral parity | Identical per-login counts |
| 9 | Leaderboard parity | Compare rendered leaderboard entries (login, count, ordering, exclusions) against PS1 output from the same cache | behavioral parity | Identical ordering and counts |
| 10 | HTML parity | Normalized diff (whitespace-insensitive, timestamp fields masked) of Python HTML vs PS1 HTML from the same cache | behavioral parity | Structurally identical: same elements, classes, IDs, data attributes, text |
| 11 | Sanity gate | Feed a deliberately broken report (empty PR table, zeroed stats) through the write path | negative boundary | Existing output file untouched without `--force-write` |
| 12 | Consumer routing | Test that html.py output changes when a ClassificationResult input changes, with html.py containing no classification vocabulary | consumer-routing | Rendering is data-driven |
| 13 | Negative bypass | A test greps `core/` (excluding classify.py) and asserts none of the classification pattern constants or their regex bodies appear | negative-bypass | Patterns exist in exactly one module |
| 14 | Timeline data parity | Chart data arrays (daily, cumulative) from timeline.py match generate-timeline.py output for the same cache | behavioral parity | Identical arrays |

## Test discipline

- Every ported function, branch, error path, and fallback gets at least one test in the same stage it is ported, not in a cleanup pass later.
- Fixtures: `make_pr()` and `make_evidence()` factories with sensible defaults and keyword overrides. Recorded gh JSON goes in `tests/fixtures/` with a comment naming the command that produced it. Fixture field names come from real gh output, never invented.
- Invariants are tested as universal properties, not case lists. Prove the corruption invariant with at least one input the implementation does not explicitly name.
- mypy strict passes on `core/` at the end of every stage. pytest green at the end of every stage. A stage is not done with either failing.
- No test asserts on log strings or console formatting.

## Stage plan

Follow the migration path in PYTHON-REWRITE.md (models/cache → classify → credit → leaderboard/github → html → entry point → timeline absorption). Gates:

1. Each stage ends with its proof-matrix rows passing plus mypy strict and full pytest green.
2. Before the classify stage, generate the PS1 baseline: run `pwsh -File generate.ps1 -OutFile <scratch>\baseline.html` against a scratch copy of the cache and keep the output and the cache snapshot for all parity rows. If the run refreshes network data, snapshot the cache after that run so both sides read identical state.
3. The parity gate (rows 4, 8, 9, 10, 14 all passing against the same snapshot) is the condition for step 9, deleting the ps1 files. Do not delete them in this session unless the gate passes and the user confirms.
4. Divergences found during parity are triaged as: Python bug (fix), PS1 bug faithfully identified (document, ask before changing behavior), or acceptable formatting drift (mask in the normalizer, document the mask).

## Out of scope

- `timeline.js`: do not touch. Step 10 in PYTHON-REWRITE.md is a separate pass for a separate session.
- Behavior improvements, new features, new repos, visual changes. This is a port; byte-level fidelity of decisions matters more than elegance of any single function. Elegance comes from the structure (owners, models, templates), not from second-guessing decision boundaries mid-port.
- Editing the published `index.html` or committing regenerated stats.

Start by reading PYTHON-REWRITE.md, then the live cache file's top-level keys, then `Get-ClosedPullRequestClassification` and its predicates in the current generate.ps1. Set up `pyproject.toml` and the package skeleton, then begin stage 1.
