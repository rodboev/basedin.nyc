from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from core.models import Cache

LEADERBOARD_CACHE_KEY_VERSION = "community-shipped-v4"
CHANGELOG_RELEASE_PROFILE = "changelog-release"
GITHUB_EVIDENCE_PROFILE = "github-evidence"
REPO_CREDIT_PROFILES = {
    "nesquena/hermes-webui": CHANGELOG_RELEASE_PROFILE,
    "kenn-io/agentsview": GITHUB_EVIDENCE_PROFILE,
    "thedotmack/claude-mem": GITHUB_EVIDENCE_PROFILE,
}
REPO_LEADERBOARD_CONFIG: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "nesquena/hermes-webui": (("nesquena",), ("nesquena-hermes",)),
    "kenn-io/agentsview": (("wesm", "mariusvniekerk", "cpcloud"), ()),
    "thedotmack/claude-mem": (("thedotmack",), ()),
    "headroomlabs-ai/headroom": (("chopratejas", "DevanshiVyas", "JerrettDavis"), ()),
    "mem0ai/mem0": (("taranjeet", "deshraj", "kartik-mem0", "chaithanyak42", "prathameshagrawal", "agumpandey"), ()),
    "stablyai/orca": (("nwparker", "AmethystLiang", "Jinwoo-H", "brennanb2025", "tmchow"), ("buf0-bot[bot]",)),
    "NVIDIA/SkillSpector": ((), ()),
    "NousResearch/hermes-agent": ((), ()),
}


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
    return folded == "dependabot[bot]"


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
    maintainers, integration_bots = REPO_LEADERBOARD_CONFIG.get(repo, ((), ()))
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


def top_credited_logins(stats: Mapping[str, LeaderboardStat], *, author: str, top: int) -> list[str]:
    refresh_logins = [
        login
        for login, _stat in sorted(
            stats.items(),
            key=lambda item: (item[1].credited, item[1].open),
            reverse=True,
        )[:top]
    ]
    if author in stats and author not in refresh_logins:
        refresh_logins.append(author)
    return refresh_logins


def community_contributor_logins(
    *,
    recent_author_logins: Iterable[str],
    exclusions: LeaderboardExclusions,
    author: str,
) -> list[str]:
    unique_logins = [
        login
        for login in _unique(recent_author_logins)
        if login and not is_leaderboard_excluded_login(login, exclusions)
    ]
    if author not in unique_logins and not is_leaderboard_excluded_login(author, exclusions):
        return [author, *unique_logins]
    return unique_logins


def merge_community_contributor_logins(
    *,
    prior_logins: Iterable[str],
    seed_logins: Iterable[str],
    recent_author_logins: Iterable[str],
    exclusions: LeaderboardExclusions,
    author: str,
) -> list[str]:
    recent = community_contributor_logins(
        recent_author_logins=recent_author_logins,
        exclusions=exclusions,
        author=author,
    )
    merged = _unique((*prior_logins, *seed_logins, *recent))
    return [login for login in merged if not is_leaderboard_excluded_login(login, exclusions)]

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
    credit_profile: str | None = None,
) -> list[CachedLeaderboardRow]:
    entry = cache.leaderboards.get(leaderboard_cache_key(repo, start_date))
    if entry is None:
        return []
    raw_stats = entry.get("stats")
    if not isinstance(raw_stats, dict):
        return []
    profile = credit_profile if credit_profile is not None else repo_credit_profile(repo)
    credited_counts = _credited_counts_for_cached_board(entry, credit_profile=profile)
    rows: list[tuple[str, LeaderboardStat]] = []
    for login, raw in raw_stats.items():
        if not isinstance(login, str) or is_leaderboard_excluded_login(login, exclusions):
            continue
        if not isinstance(raw, dict):
            continue
        stat = new_leaderboard_stat(
            total=_int_value(raw.get("total")),
            open_count=_int_value(raw.get("open")),
            recent_count=_int_value(raw.get("recentCount")),
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
        if author_login and login.lower() == author_login.lower():
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
            return {str(login): _int_value(value) for login, value in raw.items()}
    return {}

def _existing_case_key(values: Mapping[str, object], login: str) -> str:
    folded = login.lower()
    for existing in values:
        if existing.lower() == folded:
            return existing
    return login

def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0

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
