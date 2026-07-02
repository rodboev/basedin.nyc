from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

LEADERBOARD_CACHE_KEY_VERSION = "community-shipped-v4"


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


def leaderboard_cache_key(repo: str, start_date: datetime | None) -> str:
    return f"{repo}|{LEADERBOARD_CACHE_KEY_VERSION}|{start_date_cache_key(start_date)}"


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


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
