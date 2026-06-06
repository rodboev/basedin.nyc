# PR Stats

Public stats page and leaderboard for Rod Boev's open source contributions. Hosted at `basedin.nyc/pr-stats/`.

## Scripts

- `generate.ps1`: Fetches PR data from GitHub API, classifies closed PRs by reading maintainer comments, builds per-repo leaderboards with projections, generates `index.html`, and opens it in the browser. This is the single source of truth; run it to update everything.
- `pr-tracker.ps1`: CLI-only leaderboard with the same per-repo, per-author logic. Useful for quick terminal checks but redundant with generate.ps1.

PR pipeline skills (sweep, cleanup, implement, rework, review) live in the pr-sweep repo at `C:\Apps\hermes\.claude\skills\pr\`.

## Target Repos

### hermes-webui (nesquena/hermes-webui)

Nathan Esquenazi (nesquena) is co-founder of CodePath.org, based in San Francisco. GitHub account since 2008, 1,291 followers. Hermes is a side project, not a CodePath product.

**Cherry-pick workflow:** nesquena never uses GitHub's merge button. He cherry-picks commits from contributor PRs into staging branches, then his bot account `nesquena-hermes` opens "Release vX.Y.Z" PRs that get merged. Contributor PRs are closed (not merged), so `mergedAt` is always null. "Shipped" is determined by maintainer comments containing patterns like "Shipped", "cherry-picked", "merged-via", or "Salvaged into".

**Classification categories:**
- Shipped: maintainer comment references a release version
- Accepted indirectly: "Superseded by", "consolidated" (changes absorbed into another PR)
- Duplicate: "Duplicate" in comments
- Withdrawn: closed with no maintainer interaction (only Greptile bot reviews)

**Current standing (2026-06-06):** Rod is #5 on webui with 37 shipped, 32 open, 100% acceptance on resolved PRs. Top contributors are franksong2702 (244 credited), Michaelyklam (200), ai-ag2026 (148), AJV20 (75).

**Review cadence:** Comparable across contributors. franksong2702 has 244 credited out of 248 total (98% reviewed). Rod has 43 closed out of 75 total (57% reviewed). The difference is time: franksong started April 12, Rod started June 2.

**Pain point:** The hermes-webui test suite is severely broken on Windows. pytest fixtures assume POSIX paths, temp directories collide, and multiple tests fail with permission errors on NTFS. This creates friction for a Windows-native contributor since you can't validate changes locally before submitting.

### hermes-agent (NousResearch/hermes-agent)

Maintained by teknium1 (NousResearch founder) and a small team. 184K stars but the repo has ~11K open PRs with only 3 maintainers, so review throughput is very low.

**Current standing (2026-06-06):** Rod is #47 on hermes-agent with 4 credited (1 shipped, 3 indirect), 70 open. teknium1 leads at 431 credited. Review rate for external contributors is poor: hundreds of PRs sit open indefinitely.

**Pain point:** Submitting PRs here is a poor investment. The maintainer-to-PR ratio means most contributions wait weeks or months for review. The repo is listed in Tier C ("Skipped") in the contribution targets analysis.

### Contributor Ecosystem

The top hermes-webui contributors are almost all running AI coding agents. Evidence:
- **franksong2702** (248 PRs): repos include "codex-goal-writer", likely running Codex pipelines
- **ai-ag2026** (132 PRs): username literally "ai-ag", bio says "Manfred + TARS"
- **dobby-d-elf** (17 PRs): "Just a helpful elf", account created May 2026, single repo (hermes-webui fork)
- **Michaelyklam** (200 PRs): real person, TPM at Verkada in San Mateo, likely using AI agents as a side project

The two repos have almost entirely separate contributor communities. hermes-agent contributors (teknium1, OutThisLife, liuhao1024, Dusk1e, annguyenNous) rarely touch webui, and vice versa. Rod is one of the few contributors active in both.

## Technical Notes

### ConvertFrom-Json array flattening (PowerShell 5.1)

`@($raw | ConvertFrom-Json)` wraps a parsed JSON array as a single element inside another array. Fix: `@(($raw | ConvertFrom-Json) | ForEach-Object { $_ })` to enumerate items through the pipeline.

### gh pr list --limit 500 truncation

A single `gh pr list --repo X --state all --limit 500` returns the 500 most recent PRs across ALL authors. In repos with many contributors, prolific authors' older PRs fall outside this window, producing wildly inaccurate counts. Fix: discover unique author logins from the aggregate query, then fetch per-author with `--author $a --limit 500` to get full counts.

### Credited vs Total

"Credited" = closed + merged PRs (work that was reviewed). "Total" = credited + open. In the cherry-pick workflow, MERGED state only appears on nesquena-hermes release bot PRs. Contributor PRs are either CLOSED (reviewed) or OPEN (awaiting review). For Rod's PRs, "credited" is further refined to shipped + accepted indirectly via comment classification.

---

## Exploration Prompt: MemPalace Contributions

Copy the block below into a new Claude Code session to explore contribution opportunities in MemPalace.

```
I want to explore contributing to MemPalace/mempalace (54K stars, 884 stars/day, MIT license).

Background: I'm Rod Boev, a full-stack developer (React/Next.js/TypeScript/Node) with AI memory system experience. I'm a maintainer of mcp-memory-service (1.9K+ stars, batched ONNX inference, SQLite concurrency fixes) and 4th highest contributor to claude-mem (77K+ stars, spawn storm diagnosis, daemon resilience). I run Windows natively and MemPalace has first-class Windows CI.

I'm considering shifting contribution focus here from hermes-webui (broken Windows test suite) and hermes-agent (poor review throughput, ~11K open PRs with 3 maintainers).

Full target analysis with scoring dimensions, Windows CI findings, and competitor comparison is at: C:\Users\Rod\Desktop\Marketing\targets.md

Please:
1. Clone the repo and read the architecture: what backends exist, how retrieval works, what the MCP server integration looks like
2. Check the 569 open issues for good-first-issue tags or issues matching my strengths (memory backends, SQLite/concurrency, ONNX/inference, MCP integration, Windows compatibility)
3. Look at the 7 recently merged external PRs to understand what kind of contributions get accepted and how fast
4. Check the test suite: does it pass on Windows? Are there gaps I could fill?
5. Compare the codebase to mcp-memory-service: where does my existing work transfer directly?
6. Recommend 3 concrete first contributions ranked by impact and likelihood of acceptance
```
