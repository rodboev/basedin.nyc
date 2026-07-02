from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PrReportItem:
    number: int
    url: str
    repo: str
    repoLabel: str
    title: str
    classification: str
    statusKey: str
    statusLabel: str
    statusClass: str
    dateLabel: str
    releaseLabel: str
    viaLabel: str
    viaUrl: str
    createdAt: str
    closedAt: str
    mergedAt: str
    additions: int
    deletions: int
    changedFiles: int

    def to_script_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "url": self.url,
            "repo": self.repo,
            "repoLabel": self.repoLabel,
            "title": self.title,
            "classification": self.classification,
            "statusKey": self.statusKey,
            "statusLabel": self.statusLabel,
            "statusClass": self.statusClass,
            "dateLabel": self.dateLabel,
            "releaseLabel": self.releaseLabel,
            "viaLabel": self.viaLabel,
            "viaUrl": self.viaUrl,
            "createdAt": self.createdAt,
            "closedAt": self.closedAt,
            "mergedAt": self.mergedAt,
            "additions": self.additions,
            "deletions": self.deletions,
            "changedFiles": self.changedFiles,
        }


@dataclass(frozen=True)
class ReportCounts:
    total: int
    accepted: int
    open: int
    superseded: int
    lost: int
    not_shipped: int
    acceptance_rate: int | None


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


def pr_status_matches(*, filter_status_key: str, item_status_key: str, not_shipped_statuses: Iterable[str]) -> bool:
    if filter_status_key == "not-shipped":
        return item_status_key in set(not_shipped_statuses)
    return item_status_key == filter_status_key


def pr_repo_matches(*, filter_repo_key: str, item_repo_label: str) -> bool:
    return filter_repo_key == "all" or item_repo_label == filter_repo_key


def pr_filter_count(
    items: Iterable[PrReportItem],
    *,
    status_key: str,
    repo_key: str,
    not_shipped_statuses: Iterable[str],
) -> int:
    return sum(
        1
        for item in items
        if pr_status_matches(
            filter_status_key=status_key,
            item_status_key=item.statusKey,
            not_shipped_statuses=not_shipped_statuses,
        )
        and pr_repo_matches(filter_repo_key=repo_key, item_repo_label=item.repoLabel)
    )


def sort_repos_by_accepted_count(
    repos: Iterable[str],
    items: Iterable[Mapping[str, object]],
    *,
    accepted_classifications: Iterable[str],
) -> list[str]:
    accepted = set(accepted_classifications)
    item_list = list(items)
    return sorted(
        repos,
        key=lambda repo: (
            -sum(1 for item in item_list if item.get("repo") == repo and item.get("classification") in accepted),
            repo,
        ),
    )


def report_items_to_script_dicts(items: Iterable[PrReportItem]) -> list[dict[str, object]]:
    return [item.to_script_dict() for item in items]


def report_item_from_script_dict(raw: Mapping[str, object]) -> PrReportItem:
    return PrReportItem(
        number=_int_value(raw.get("number")),
        url=_string_value(raw.get("url")),
        repo=_string_value(raw.get("repo")),
        repoLabel=_string_value(raw.get("repoLabel")),
        title=_string_value(raw.get("title")),
        classification=_string_value(raw.get("classification")),
        statusKey=_string_value(raw.get("statusKey")),
        statusLabel=_string_value(raw.get("statusLabel")),
        statusClass=_string_value(raw.get("statusClass")),
        dateLabel=_string_value(raw.get("dateLabel")),
        releaseLabel=_string_value(raw.get("releaseLabel")),
        viaLabel=_string_value(raw.get("viaLabel")),
        viaUrl=_string_value(raw.get("viaUrl")),
        createdAt=_string_value(raw.get("createdAt")),
        closedAt=_string_value(raw.get("closedAt")),
        mergedAt=_string_value(raw.get("mergedAt")),
        additions=_int_value(raw.get("additions")),
        deletions=_int_value(raw.get("deletions")),
        changedFiles=_int_value(raw.get("changedFiles")),
    )


def report_items_from_script_dicts(items: Iterable[Mapping[str, object]]) -> list[PrReportItem]:
    return [report_item_from_script_dict(item) for item in items]


def sort_report_items_by_effective_date(items: Iterable[PrReportItem]) -> list[PrReportItem]:
    return sorted(items, key=_report_item_sort_datetime, reverse=True)


def status_filter_dicts(
    items: Iterable[PrReportItem],
    filters: Iterable[tuple[str, str]],
    *,
    repo_key: str,
    not_shipped_statuses: Iterable[str],
) -> list[dict[str, object]]:
    item_list = list(items)
    return [
        {
            "key": key,
            "label": label,
            "count": pr_filter_count(
                item_list,
                status_key=key,
                repo_key=repo_key,
                not_shipped_statuses=not_shipped_statuses,
            ),
        }
        for key, label in filters
    ]


def report_counts(
    items: Iterable[PrReportItem],
    *,
    accepted_classifications: Iterable[str],
    open_status: str,
    superseded_status: str,
    lost_status: str,
) -> ReportCounts:
    item_list = list(items)
    accepted = set(accepted_classifications)
    accepted_count = sum(1 for item in item_list if item.classification in accepted)
    open_count = sum(1 for item in item_list if item.classification == open_status)
    superseded_count = sum(1 for item in item_list if item.classification == superseded_status)
    lost_count = sum(1 for item in item_list if item.classification == lost_status)
    not_shipped = superseded_count + lost_count
    acceptance_closed = accepted_count + not_shipped
    rate = round((accepted_count / acceptance_closed) * 100) if acceptance_closed > 0 else None
    return ReportCounts(
        total=len(item_list),
        accepted=accepted_count,
        open=open_count,
        superseded=superseded_count,
        lost=lost_count,
        not_shipped=not_shipped,
        acceptance_rate=rate,
    )


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


def _report_item_sort_datetime(item: PrReportItem) -> datetime:
    parsed = _parse_datetime(
        pull_request_effective_iso_date(
            status_key=item.statusKey,
            created_at=item.createdAt,
            closed_at=item.closedAt,
        ),
    )
    return parsed if parsed is not None else datetime.min.replace(tzinfo=timezone.utc)


def _string_value(value: object) -> str:
    scalar = scalar_value(value)
    return scalar if isinstance(scalar, str) else str(scalar)


def _int_value(value: object) -> int:
    scalar = scalar_value(value)
    if isinstance(scalar, bool):
        return 0
    if isinstance(scalar, int):
        return scalar
    if isinstance(scalar, float):
        return int(scalar)
    if isinstance(scalar, str):
        try:
            return int(scalar)
        except ValueError:
            return 0
    return 0
