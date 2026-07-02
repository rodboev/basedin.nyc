from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def repo_label(repo: str) -> str:
    short = repo.rsplit("/", 1)[-1]
    if short == "hermes-webui":
        return "webui"
    if short == "github-mcp-server":
        return "gh-mcp"
    if short == "GenericAgent":
        return "generic-agent"
    return short


def pull_request_effective_iso_date(*, status_key: str, created_at: str, closed_at: str) -> str:
    if status_key == "open":
        return created_at
    return closed_at or created_at


def format_eastern_date(iso_date: str) -> str:
    parsed = _parse_datetime(iso_date)
    if parsed is None:
        return ""
    eastern = parsed.astimezone(EASTERN)
    hour = eastern.hour % 12 or 12
    suffix = "AM" if eastern.hour < 12 else "PM"
    return f"{eastern.month}/{eastern.day}/{eastern.year % 100:02d} {hour}:{eastern.minute:02d} {suffix}"


def scalar_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, list):
        return scalar_value(value[0]) if value else ""
    if isinstance(value, tuple):
        return scalar_value(value[0]) if value else ""
    return value


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
