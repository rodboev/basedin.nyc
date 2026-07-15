from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.github import run_gh
from core.models import Cache, int_value
from core.releases import release_credit_counts

LEADERBOARD_CACHE_KEY_VERSION = "community-shipped-v4"
LEADERBOARD_TTL_SECONDS = 24 * 3600
CHANGELOG_RELEASE_PROFILE = "changelog-release"
GITHUB_EVIDENCE_PROFILE = "github-evidence"
REPO_CREDIT_PROFILES = {
    "nesquena/hermes-webui": CHANGELOG_RELEASE_PROFILE,
    "kenn-io/agentsview": GITHUB_EVIDENCE_PROFILE,
    "thedotmack/claude-mem": GITHUB_EVIDENCE_PROFILE,
}
LEADERBOARD_PAGE_SIZE = 100
LEADERBOARD_MAX_PAGES = 200
COMMUNITY_PR_QUERY = """\
query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: $pageSize, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { state createdAt author { login __typename } }
    }
  }
}
"""
DEFAULT_OVERLAY_CONFIG_DIR = Path(r"D:\Repos\.claude\pr-sweep\repos")
_MEMBER_LINE = re.compile(r"^\s{2,}-\s+(\S+?)(?::|\s|$)")
_overlay_dir: Path = DEFAULT_OVERLAY_CONFIG_DIR
_overlay_cache: dict[str, tuple[str, ...]] = {}


def set_overlay_config_dir(path: Path) -> None:
    global _overlay_dir
    _overlay_dir = path
    _overlay_cache.clear()


def _load_overlay_members(repo_short: str) -> tuple[str, ...]:
    if repo_short in _overlay_cache:
        return _overlay_cache[repo_short]
    config_path = _overlay_dir / repo_short / "config.md"
    members: list[str] = []
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        print(
            f"WARNING: missing pr-sweep overlay config {config_path}; maintainer exclusions and gating for {repo_short} disabled",
            file=sys.stderr,
        )
        _overlay_cache[repo_short] = ()
        return ()
    in_members = False
    for line in text.splitlines():
        if line.strip().lower() == "- members:":
            in_members = True
            continue
        if in_members:
            m = _MEMBER_LINE.match(line)
            if m:
                members.append(m.group(1))
            else:
                break
    result = tuple(members)
    _overlay_cache[repo_short] = result
    return result


# Integration bots are privileged commenters for classification (Evidence.integration_bots),
# not humans, so they never appear in overlay Members blocks and stay hardcoded here.
REPO_INTEGRATION_BOTS: dict[str, tuple[str, ...]] = {
    "stablyai/orca": ("buf0-bot[bot]",),
}


def repo_leaderboard_config(repo: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    repo_short = repo.rsplit("/", 1)[-1].lower()
    return _load_overlay_members(repo_short), REPO_INTEGRATION_BOTS.get(repo, ())


@dataclass(frozen=True)
class LeaderboardExclusions:
    owner: str = ""
    maintainers: tuple[str, ...] = ()
    integration_bots: tuple[str, ...] = ()

    @property
    def all(self) -> tuple[str, ...]:
        return tuple(_unique(login for login in (self.owner, *self.maintainers, *self.integration_bots) if login))


@dataclass(frozen=True)
class LeaderboardStat:
    credited: int
    open: int
    total: int
    recentCount: int
    rate: float
    idle: float
    lastCreatedAt: str
    estimated: bool = False
    shippedClassified: bool = False

@dataclass(frozen=True)
class CachedLeaderboardRow:
    rank: int
    login: str
    credited: int
    open: int
    rate: float
    idle: float = 999.0


def is_leaderboard_bot(login: str) -> bool:
    if not login:
        return True
    folded = login.lower()
    if folded.startswith("app/"):
        return True
    return folded.endswith("[bot]")


def is_leaderboard_excluded_login(login: str, exclusions: LeaderboardExclusions) -> bool:
    if not login:
        return True
    if is_leaderboard_bot(login):
        return True
    folded = login.lower()
    return any(candidate.lower() == folded for candidate in exclusions.all)


def repo_leaderboard_exclusions(
    *,
    owner: str,
    maintainer_logins: Iterable[str] = (),
    integration_bots: Iterable[str] = (),
) -> LeaderboardExclusions:
    return LeaderboardExclusions(
        owner=owner,
        maintainers=tuple(login for login in maintainer_logins if login),
        integration_bots=tuple(login for login in integration_bots if login),
    )


def configured_repo_leaderboard_exclusions(repo: str) -> LeaderboardExclusions:
    maintainers, integration_bots = repo_leaderboard_config(repo)
    return repo_leaderboard_exclusions(
        owner=repo.split("/", 1)[0],
        maintainer_logins=maintainers,
        integration_bots=integration_bots,
    )


def leaderboard_cache_key(repo: str, start_date: datetime | None) -> str:
    return f"{repo}|{LEADERBOARD_CACHE_KEY_VERSION}|{start_date_cache_key(start_date)}"


def repo_credit_profile(repo: str) -> str:
    return REPO_CREDIT_PROFILES.get(repo, GITHUB_EVIDENCE_PROFILE)


def start_date_cache_key(date: datetime | None) -> str:
    if date is None:
        return "all"
    return date.strftime("%Y-%m-%d")


def new_leaderboard_stat(
    *,
    total: int,
    open_count: int,
    recent_count: int,
    last_created_at: str,
    now: datetime,
    rate_window_days: float,
) -> LeaderboardStat:
    last = _parse_datetime(last_created_at)
    idle = round((now - last).total_seconds() / 86_400, 1) if last is not None else 999
    return LeaderboardStat(
        credited=max(0, total - open_count),
        open=open_count,
        total=total,
        recentCount=recent_count,
        rate=round(recent_count / rate_window_days, 1) if rate_window_days > 0 else 0,
        idle=idle,
        lastCreatedAt=last_created_at if last_created_at else "",
    )


def cached_leaderboard_rows(
    *,
    cache: Cache,
    repo: str,
    exclusions: LeaderboardExclusions,
    now: datetime,
    rate_window_days: float,
    start_date: datetime | None = None,
    max_entries: int | None = 50,
    author_login: str = "",
    author_credited: int = 0,
    author_open: int = 0,
    author_recent_created: Iterable[str] | None = None,
    credit_profile: str | None = None,
) -> list[CachedLeaderboardRow]:
    entry = cache.leaderboards.get(leaderboard_cache_key(repo, start_date))
    if entry is None:
        return []
    raw_stats = entry.get("stats")
    if not isinstance(raw_stats, dict):
        return []
    author_recent = _author_recent_count(
        entry,
        author_recent_created,
        window_days=rate_window_days,
    )
    profile = credit_profile if credit_profile is not None else repo_credit_profile(repo)
    credited_counts = _credited_counts_for_cached_board(entry, credit_profile=profile)
    rows: list[tuple[str, LeaderboardStat]] = []
    for login, raw in raw_stats.items():
        if not isinstance(login, str) or is_leaderboard_excluded_login(login, exclusions):
            continue
        if not isinstance(raw, dict):
            continue
        is_author = bool(author_login) and login.lower() == author_login.lower()
        # The cached scan counts every PR the author opened; withdrawn ones are excluded from
        # totals everywhere else on the page, so keep them out of the rate too.
        recent_count = (
            author_recent
            if is_author and author_recent is not None
            else int_value(raw.get("recentCount"))
        )
        stat = new_leaderboard_stat(
            total=int_value(raw.get("total")),
            open_count=int_value(raw.get("open")),
            recent_count=recent_count,
            last_created_at=_string_value(raw.get("lastCreatedAt")),
            now=now,
            rate_window_days=rate_window_days,
        )
        credited_key = _existing_case_key(credited_counts, login)
        credited = credited_counts.get(
            credited_key,
            0 if profile == CHANGELOG_RELEASE_PROFILE else stat.credited,
        )
        open_count = stat.open
        if is_author:
            credited = author_credited
            open_count = author_open
        rows.append((
            login,
            LeaderboardStat(
                credited=credited,
                open=open_count,
                total=credited + open_count,
                recentCount=stat.recentCount,
                rate=stat.rate,
                idle=stat.idle,
                lastCreatedAt=stat.lastCreatedAt,
                estimated=False,
                shippedClassified=True,
            ),
        ))
    sorted_rows = sorted(rows, key=lambda item: (item[1].credited, item[1].open), reverse=True)
    if max_entries is not None:
        sorted_rows = sorted_rows[:max_entries]
    return [
        CachedLeaderboardRow(rank=rank, login=login, credited=stat.credited, open=stat.open, rate=stat.rate, idle=stat.idle)
        for rank, (login, stat) in enumerate(sorted_rows, start=1)
    ]


def _author_recent_count(
    entry: Mapping[str, object],
    created_ats: Iterable[str] | None,
    *,
    window_days: float,
) -> int | None:
    if created_ats is None:
        return None
    # Anchored to cachedAt, not now: every other login's recentCount was counted against the
    # window that ran when the board was scanned, so the author's has to use the same one to
    # stay comparable in the leaderboard's rate column.
    cached_at = _parse_datetime(_string_value(entry.get("cachedAt")))
    if cached_at is None:
        return None
    cutoff = cached_at - timedelta(days=window_days)
    count = 0
    for raw in created_ats:
        created = _parse_datetime(raw)
        # Bounded at both ends. The scan needed no upper bound (nothing was newer than the run
        # that wrote it), but this counts at render time, when the author has PRs the board has
        # never seen; without the bound a stale board stretches the window past its window_days.
        if created is not None and cutoff <= created < cached_at:
            count += 1
    return count


def fetch_community_leaderboard(repo: str, cache: Cache, *, now: datetime) -> bool:
    cache_key = leaderboard_cache_key(repo, None)
    existing = cache.leaderboards.get(cache_key)
    if existing is not None and not _leaderboard_entry_expired(existing, now=now):
        return False
    nodes = _fetch_community_pr_nodes(repo)
    if nodes is None:
        return False

    exclusions = configured_repo_leaderboard_exclusions(repo)
    recent_cutoff = now.timestamp() - 7 * 86_400
    totals: dict[str, int] = {}
    opens: dict[str, int] = {}
    merged: dict[str, int] = {}
    recents: dict[str, int] = {}
    last_dates: dict[str, str] = {}

    for node in nodes:
        author_raw = node.get("author")
        if not isinstance(author_raw, dict):
            continue
        # GraphQL Bot actors carry plain logins without the [bot] suffix.
        if author_raw.get("__typename") == "Bot":
            continue
        login = str(author_raw.get("login") or "")
        if not login or is_leaderboard_excluded_login(login, exclusions):
            continue
        state = node.get("state", "")
        created_at = str(node.get("createdAt", ""))
        totals[login] = totals.get(login, 0) + 1
        if state == "OPEN":
            opens[login] = opens.get(login, 0) + 1
        if state == "MERGED":
            merged[login] = merged.get(login, 0) + 1
        if created_at:
            if created_at > last_dates.get(login, ""):
                last_dates[login] = created_at
            try:
                if datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp() >= recent_cutoff:
                    recents[login] = recents.get(login, 0) + 1
            except ValueError:
                pass

    # An empty community is still a complete scan; stamp the entry so the TTL
    # prevents re-paging the whole repo on every run.
    logins = sorted(totals, key=lambda login: totals[login], reverse=True)
    now_str = now.isoformat().replace("+00:00", "Z")
    # Merge into the existing entry: PS1-era boards carry releaseCreditCounts and
    # other credit keys that Python has no writer for and must not destroy.
    entry: dict[str, object] = dict(existing) if existing is not None else {}
    entry.update({
        "cachedAt": now_str,
        "refreshedAt": now_str,
        "logins": logins,
        "stats": {
            login: {"total": totals[login], "open": opens.get(login, 0),
                    "recentCount": recents.get(login, 0), "lastCreatedAt": last_dates.get(login, "")}
            for login in logins
        },
        "shippedCounts": _merged_shipped_counts(
            existing,
            logins=logins,
            merged=merged,
            release_credited=release_credit_counts(cache, repo) if repo_credit_profile(repo) != CHANGELOG_RELEASE_PROFILE else None,
        ),
    })
    cache.leaderboards[cache_key] = entry
    print(f"  Built leaderboard for {repo}: {len(nodes)} PRs, {len(logins)} contributors", file=sys.stderr)
    return True


def _leaderboard_entry_expired(entry: Mapping[str, object], *, now: datetime) -> bool:
    cached_at = entry.get("cachedAt")
    if not isinstance(cached_at, str) or not cached_at:
        return True
    cached_time = _parse_datetime(cached_at)
    if cached_time is None:
        return True
    return (now - cached_time).total_seconds() >= LEADERBOARD_TTL_SECONDS


def _fetch_community_pr_nodes(repo: str) -> list[dict[str, object]] | None:
    owner, _, name = repo.partition("/")
    nodes: list[dict[str, object]] = []
    cursor = ""
    for _page in range(LEADERBOARD_MAX_PAGES):
        args = [
            "api", "graphql",
            "-f", f"query={COMMUNITY_PR_QUERY}",
            "-F", f"owner={owner}",
            "-F", f"name={name}",
            "-F", f"pageSize={LEADERBOARD_PAGE_SIZE}",
        ]
        if cursor:
            args.extend(["-F", f"cursor={cursor}"])
        raw = run_gh(*args, suppress_errors=True)
        if not raw:
            # A failed page would truncate the board; keep the existing entry instead.
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        connection = _pull_request_connection(payload)
        if connection is None:
            return None
        page_nodes = connection.get("nodes")
        if isinstance(page_nodes, list):
            nodes.extend(node for node in page_nodes if isinstance(node, dict))
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            return nodes
        cursor = str(page_info.get("endCursor") or "")
        if not cursor:
            return nodes
    # Cap exhausted with pages remaining: abort rather than store a truncated board.
    return None


def _pull_request_connection(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    repository = data.get("repository")
    if not isinstance(repository, dict):
        return None
    connection = repository.get("pullRequests")
    return connection if isinstance(connection, dict) else None


def _merged_shipped_counts(
    existing: Mapping[str, object] | None,
    *,
    logins: Iterable[str],
    merged: Mapping[str, int],
    release_credited: Mapping[str, int] | None = None,
) -> dict[str, int]:
    prior_raw = existing.get("shippedCounts") if existing is not None else None
    prior = {str(login): int_value(value) for login, value in prior_raw.items()} if isinstance(prior_raw, dict) else {}
    counts: dict[str, int] = dict(prior)
    rc = release_credited or {}
    for login in logins:
        prior_key = _existing_case_key(counts, login)
        prior_count = counts.pop(prior_key, 0)
        counts[login] = max(prior_count, merged.get(login, 0), rc.get(login, 0))
    return counts


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

def _credited_counts_for_cached_board(entry: Mapping[str, object], *, credit_profile: str) -> dict[str, int]:
    keys = ("releaseCreditCounts",) if credit_profile == CHANGELOG_RELEASE_PROFILE else ("shippedCounts",)
    for key in keys:
        raw = entry.get(key)
        if isinstance(raw, dict) and raw:
            return {str(login): int_value(value) for login, value in raw.items()}
    return {}

def _existing_case_key(values: Mapping[str, object], login: str) -> str:
    folded = login.lower()
    for existing in values:
        if existing.lower() == folded:
            return existing
    return login

def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""

def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%m/%d/%Y %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
