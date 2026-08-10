from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor

from core.github import GhRetryExhausted, run_gh

_display_names: dict[str, str] = {}


def set_repo_display_names(mapping: Mapping[str, str]) -> None:
    _display_names.clear()
    _display_names.update(mapping)


def display_repo(repo: str) -> str:
    return _display_names.get(repo, repo)


def resolve_canonical_repos(repos: Iterable[str], *, workers: int = 4) -> dict[str, str]:
    """Map every repos.txt entry to the repo's current owner/name.

    `gh api repos/...` and GraphQL `repository()` follow transfer redirects, but the search
    API's `repo:` qualifier does not and silently matches nothing, so a transferred repo
    reports zero author PRs while its leaderboard still builds.
    """
    entries = list(repos)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        resolved = list(pool.map(_canonical_repo_name, entries))
    missing = [entry for entry, name in zip(entries, resolved) if not name]
    if missing:
        raise ValueError(f"repos.txt entries not found on GitHub: {', '.join(missing)}")
    canonical_by_entry = dict(zip(entries, resolved))
    entry_by_canonical: dict[str, str] = {}
    for entry, name in canonical_by_entry.items():
        if name in entry_by_canonical:
            raise ValueError(f"repos.txt lists both {entry_by_canonical[name]} and {entry}; both resolve to {name}")
        entry_by_canonical[name] = entry
    return canonical_by_entry


def _canonical_repo_name(repo: str) -> str:
    try:
        return run_gh("api", f"repos/{repo}", "--jq", ".full_name", suppress_errors=True).strip()
    except GhRetryExhausted as exc:
        # Transient gh failure: assume no rename rather than aborting the whole run.
        print(f"WARNING: could not resolve {repo}, using it as written: {exc}", file=sys.stderr)
        return repo
