from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.classify import ClassificationResult
from core.github import GhPullRequestView

EASTERN = ZoneInfo("America/New_York")
CLASSIFICATION_STATUS_META: dict[str, tuple[str, str, str]] = {
    "shipped": (
        "Shipped",
        "tag-shipped",
        "Verified via merged release PR, maintainer release evidence, or indirect accepted sibling",
    ),
    "accepted-indirect": ("Shipped", "tag-shipped", ""),
    "open": ("Open", "tag-open", "Pending review"),
    "withdrawn": ("Withdrawn", "tag-withdrawn", "Closed without maintainer action"),
    "superseded": ("Superseded", "tag-superseded", "Replaced by a newer PR"),
    "lost": ("Lost", "tag-lost", "Closed without acceptance"),
}


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
    evidenceKind: str = ""

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


@dataclass(frozen=True)
class ReportActivitySummary:
    time_span: str
    time_range: str


@dataclass(frozen=True)
class ReportBarItem:
    key: str
    label: str
    count: int
    width: float
    title: str
    content: str


@dataclass(frozen=True)
class ReportStatusRow:
    label: str
    tag_class: str
    count: int
    details: str


@dataclass(frozen=True)
class RepresentativeItem:
    number: int
    url: str
    repo: str
    repoLabel: str
    desc: str
    release: str = ""
    releaseUrl: str = ""
    viaLabel: str = ""
    viaUrl: str = ""
    classification: str = ""


REPRESENTATIVE_BLOCK_HEADING = "Representative merged PRs:"
_REPRESENTATIVE_LINE = re.compile(r"^-\s*\[#(\d+)\]\(([^)]+)\)")
_REPRESENTATIVE_RELEASE_SUFFIX = re.compile(r"\s*\(\[([^\]]+)\]\(([^)]+)\)\)\s*$")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_PULL_URL_REPO = re.compile(r"github\.com/([^/]+/[^/]+)/pull/")


def parse_representative_readme(readme_text: str) -> list[RepresentativeItem]:
    items: list[RepresentativeItem] = []
    in_block = False
    for line in readme_text.split("\n"):
        if line.startswith(REPRESENTATIVE_BLOCK_HEADING):
            in_block = True
            continue
        if not in_block:
            continue
        if line.startswith("##") or (not line.startswith("-") and items and not re.match(r"^\s", line)):
            break
        match = _REPRESENTATIVE_LINE.match(line)
        if match is None:
            continue
        number = int(match.group(1))
        url = match.group(2)
        desc = _REPRESENTATIVE_LINE.sub("", line, count=1)
        desc = re.sub(r"^\W+\s*", "", desc)
        release = ""
        release_url = ""
        release_match = _REPRESENTATIVE_RELEASE_SUFFIX.search(desc)
        if release_match:
            release = release_match.group(1)
            release_url = release_match.group(2)
            desc = _REPRESENTATIVE_RELEASE_SUFFIX.sub("", desc)
        desc = _MARKDOWN_LINK.sub(r'<a href="\2">\1</a>', desc).rstrip()
        repo_match = _PULL_URL_REPO.search(url)
        repo = repo_match.group(1) if repo_match else ""
        items.append(
            RepresentativeItem(
                number=number,
                url=url,
                repo=repo,
                repoLabel=repo_label(repo) if repo else "",
                desc=desc,
                release=release,
                releaseUrl=release_url,
            ),
        )
    return items


def enrich_representative_items(
    items: Iterable[RepresentativeItem],
    report_items: Iterable[PrReportItem],
) -> list[RepresentativeItem]:
    by_key = {(item.repo, item.number): item for item in report_items}
    enriched: list[RepresentativeItem] = []
    for item in items:
        matched = by_key.get((item.repo, item.number))
        if matched is None:
            enriched.append(item)
            continue
        release = item.release
        release_url = item.releaseUrl
        if not release and matched.releaseLabel and matched.releaseLabel != "indirect":
            release = matched.releaseLabel
            release_url = f"https://github.com/{item.repo}/releases/tag/{release}"
        elif not release and matched.classification == "accepted-indirect":
            release = "indirect"
        enriched.append(
            replace(
                item,
                release=release,
                releaseUrl=release_url,
                viaLabel=matched.viaLabel,
                viaUrl=matched.viaUrl,
                classification=matched.classification,
            ),
        )
    return enriched


def repo_label(repo: str) -> str:
    short = repo.rsplit("/", 1)[-1]
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
        evidenceKind=_string_value(raw.get("evidenceKind")),
    )


def report_items_from_script_dicts(items: Iterable[Mapping[str, object]]) -> list[PrReportItem]:
    return [report_item_from_script_dict(item) for item in items]


def report_item_from_pull_request_view(
    *,
    repo: str,
    pr: GhPullRequestView,
    classification: ClassificationResult,
) -> PrReportItem:
    classification_key = classification.classification or "open"
    status_key = "shipped" if classification_key == "accepted-indirect" else classification_key
    label, tag, _desc = CLASSIFICATION_STATUS_META.get(classification_key, CLASSIFICATION_STATUS_META["open"])
    release_label = ""
    if classification_key == "accepted-indirect":
        release_label = "indirect"
    elif classification_key == "shipped" and classification.release:
        release_label = classification.release

    created_at = pr.createdAt
    closed_at = pr.closedAt or ""
    effective_date = pull_request_effective_iso_date(status_key=status_key, created_at=created_at, closed_at=closed_at)
    return PrReportItem(
        number=pr.number,
        url=pr.url or f"https://github.com/{repo}/pull/{pr.number}",
        repo=repo,
        repoLabel=repo_label(repo),
        title=pr.title,
        classification=classification_key,
        statusKey=status_key,
        statusLabel=label,
        statusClass=tag,
        dateLabel=format_eastern_date(effective_date),
        releaseLabel=release_label,
        viaLabel=classification.via_label,
        viaUrl=classification.via_url,
        createdAt=created_at,
        closedAt=closed_at,
        mergedAt=pr.mergedAt or "",
        additions=pr.additions,
        deletions=pr.deletions,
        changedFiles=pr.changedFiles,
        evidenceKind=classification.evidence_kind,
    )


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


def repo_filter_dicts(repos: Iterable[str]) -> list[dict[str, str]]:
    filters = [{"key": "all", "label": "All"}]
    filters.extend({"key": repo_label(repo), "label": repo_label(repo)} for repo in repos)
    return filters


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
        total=accepted_count + open_count + not_shipped,
        accepted=accepted_count,
        open=open_count,
        superseded=superseded_count,
        lost=lost_count,
        not_shipped=not_shipped,
        acceptance_rate=rate,
    )


def report_activity_summary(items: Iterable[PrReportItem]) -> ReportActivitySummary:
    dates = sorted(
        parsed
        for parsed in (
            _parse_datetime(
                pull_request_effective_iso_date(
                    status_key=item.classification or "open",
                    created_at=item.createdAt,
                    closed_at=item.closedAt,
                ),
            )
            for item in items
        )
        if parsed is not None
    )
    if len(dates) < 2:
        return ReportActivitySummary(time_span="N/A", time_range="")

    active_days = len({date.date() for date in dates})
    time_span = "1 day" if active_days == 1 else f"{active_days} days"
    display_end = dates[0].date().toordinal() + (dates[-1] - dates[0]).days
    end_date = datetime.fromordinal(display_end)
    time_range = f"Active days from {_format_month_day(dates[0])} - {_format_month_day(end_date)}"
    return ReportActivitySummary(time_span=time_span, time_range=time_range)


def report_bar_items(counts: ReportCounts) -> list[ReportBarItem]:
    specs = (
        ("shipped", "Shipped", counts.accepted, "wide", "wide"),
        ("superseded", "Superseded", counts.superseded, "never", "wide"),
        ("lost", "Lost", counts.lost, "never", "wide"),
        ("open", "Open", counts.open, "always", "always"),
    )
    items: list[ReportBarItem] = []
    for key, label, count, title_mode, content_mode in specs:
        pct = round((count / counts.total) * 100, 1) if counts.total > 0 else 0.0
        wide = pct > 4
        title = str(count) if title_mode == "always" or (title_mode == "wide" and wide) else ""
        content = str(count) if content_mode == "always" or wide else ""
        items.append(ReportBarItem(key=key, label=label, count=count, width=pct, title=title, content=content))
    return items


def default_status_filter_dicts(counts: ReportCounts) -> list[dict[str, object]]:
    return [
        {"key": "open", "label": "Open", "count": counts.open},
        {"key": "shipped", "label": "Shipped", "count": counts.accepted},
        {"key": "not-shipped", "label": "Not Shipped", "count": counts.not_shipped},
    ]


def repo_status_rows(items: Iterable[PrReportItem]) -> list[ReportStatusRow]:
    item_list = list(items)
    counts = Counter(item.classification for item in item_list)
    has_cherry_pick = any(item.evidenceKind == "timeline" for item in item_list)
    has_indirect = any(item.evidenceKind == "accepted-indirect" or item.classification == "accepted-indirect" for item in item_list)
    shipped_desc = _shipped_details(has_cherry_pick=has_cherry_pick, has_indirect=has_indirect)

    rows: list[ReportStatusRow] = []
    for status in ("shipped", "open", "superseded", "lost"):
        count = counts["shipped"] + counts["accepted-indirect"] if status == "shipped" else counts[status]
        if count == 0 and status not in {"shipped", "open"}:
            continue
        label, tag, desc = CLASSIFICATION_STATUS_META[status]
        rows.append(ReportStatusRow(label=label, tag_class=tag, count=count, details=shipped_desc if status == "shipped" else desc))
    return rows


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


def _format_month_day(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day}"


def _shipped_details(*, has_cherry_pick: bool, has_indirect: bool) -> str:
    if has_cherry_pick and has_indirect:
        return "Merged, cherry-picked, and release-credited"
    if has_cherry_pick:
        return "Merged and cherry-picked"
    if has_indirect:
        return "Merged and release-credited"
    return "Merged"


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
