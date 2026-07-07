from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, TypeVar, cast

from pydantic import TypeAdapter, ValidationError

from core.models import Cache, ClassificationEntry, JsonObject

CACHE_VERSION: Final = 3
CLASSIFICATION_TTL_PROFILES: Final[dict[str, tuple[tuple[int | None, int], ...]]] = {
    "stable": ((30, 30), (120, 90), (None, 180)),
    "timeline": ((14, 14), (60, 30), (None, 90)),
    "volatile": ((14, 7), (60, 30), (None, 90)),
}
CLASSIFICATION_TTL_PROFILE_FOR: Final[dict[str, str]] = {
    "shipped/direct-merge": "stable",
    "shipped/timeline": "timeline",
    "shipped/*": "volatile",
    "accepted-indirect/*": "volatile",
    "lost/*": "stable",
    "withdrawn/*": "stable",
}
DEFAULT_CLOSED_CLASSIFICATION_TTL_HOURS: Final = 24 * 30

_TOP_LEVEL_SECTIONS: Final[tuple[str, ...]] = (
    "entries",
    "authorPulls",
    "authorPullScanMeta",
    "leaderboards",
    "contributorsMdSeeds",
    "prAuthorsByNumber",
    "prPullStates",
    "commitCreditMap",
    "absorbCommitMap",
    "mergedPrCreditMap",
    "absorbedCreditMap",
    "shipCommentClassifications",
    "commitScanMeta",
)

_T = TypeVar("_T")
_CLASSIFICATION_ENTRIES = TypeAdapter(dict[str, ClassificationEntry])
_JSON_OBJECT_MAP = TypeAdapter(dict[str, JsonObject])
_STRING_MAP = TypeAdapter(dict[str, str])


def empty_cache(*, invalid_sections: frozenset[str] = frozenset()) -> Cache:
    return Cache(version=CACHE_VERSION, invalid_sections=invalid_sections)


def load_cache(path: str | Path) -> Cache:
    try:
        raw_text = Path(path).read_text(encoding="utf-8")
        raw_value = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return empty_cache(invalid_sections=frozenset(_TOP_LEVEL_SECTIONS))

    if not isinstance(raw_value, dict):
        return empty_cache(invalid_sections=frozenset(_TOP_LEVEL_SECTIONS))

    return _cache_from_mapping(cast(dict[str, object], raw_value))


def save_cache(cache: Cache, path: str | Path) -> None:
    payload = cache.model_dump(mode="json", exclude={"invalid_sections"})
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def dump_cache_json(cache: Cache) -> str:
    payload = cache.model_dump(mode="json", exclude={"invalid_sections"})
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def get_closed_classification_cache_ttl_hours(
    *,
    closed_at: str | None,
    classification: str,
    evidence_kind: str,
    now: datetime,
    default_ttl_hours: int = DEFAULT_CLOSED_CLASSIFICATION_TTL_HOURS,
) -> int:
    closed_at_datetime = _parse_datetime(closed_at)
    age_days = (now - closed_at_datetime).total_seconds() / 86_400 if closed_at_datetime else 999.0
    profile_name = CLASSIFICATION_TTL_PROFILE_FOR.get(f"{classification}/{evidence_kind}")
    if profile_name is None:
        profile_name = CLASSIFICATION_TTL_PROFILE_FOR.get(f"{classification}/*")
    if profile_name is None:
        return default_ttl_hours

    for max_age_days, ttl_days in CLASSIFICATION_TTL_PROFILES[profile_name]:
        if max_age_days is None or age_days < max_age_days:
            return 24 * ttl_days
    return default_ttl_hours


def get_cached_closed_classification(
    cache: Cache,
    *,
    repo: str,
    number: int,
    now: datetime,
    ttl_hours: int,
) -> ClassificationEntry | None:
    entry = cache.entries.get(classification_cache_key(repo, number))
    if entry is None or not entry.classification or entry.classification == "open":
        return None

    cached_at = _parse_datetime(entry.cachedAt)
    if cached_at is None:
        return None

    if (now - cached_at).total_seconds() / 3_600 > ttl_hours:
        return None
    return entry


def set_cached_closed_classification(
    cache: Cache,
    *,
    repo: str,
    number: int,
    classification: str,
    release: str,
    via_label: str,
    via_url: str,
    evidence_kind: str,
    now: datetime,
) -> None:
    key = classification_cache_key(repo, number)
    if classification and classification != "open":
        cache.entries[key] = ClassificationEntry(
            classification=classification,
            release=release,
            viaLabel=via_label,
            viaUrl=via_url,
            evidenceKind=evidence_kind,
            cachedAt=_format_datetime(now),
        )
    else:
        cache.entries.pop(key, None)


def classification_cache_key(repo: str, number: int) -> str:
    return f"{repo}#{number}"


def _cache_from_mapping(raw: dict[str, object]) -> Cache:
    invalid_sections: set[str] = set()
    version = raw.get("version")
    if not isinstance(version, int):
        version = CACHE_VERSION
        invalid_sections.add("version")

    entries: dict[str, ClassificationEntry] = {}
    if version == CACHE_VERSION:
        entries = _load_section(raw, "entries", _CLASSIFICATION_ENTRIES, invalid_sections)
    elif "entries" in raw:
        invalid_sections.add("entries")

    return Cache(
        version=CACHE_VERSION,
        entries=entries,
        authorPulls=_load_section(raw, "authorPulls", _JSON_OBJECT_MAP, invalid_sections),
        authorPullScanMeta=_load_section(raw, "authorPullScanMeta", _JSON_OBJECT_MAP, invalid_sections),
        leaderboards=_load_section(raw, "leaderboards", _JSON_OBJECT_MAP, invalid_sections),
        contributorsMdSeeds=_load_section(raw, "contributorsMdSeeds", _JSON_OBJECT_MAP, invalid_sections),
        prAuthorsByNumber=_load_section(raw, "prAuthorsByNumber", _STRING_MAP, invalid_sections),
        prPullStates=_load_section(raw, "prPullStates", _JSON_OBJECT_MAP, invalid_sections),
        commitCreditMap=_load_section(raw, "commitCreditMap", _JSON_OBJECT_MAP, invalid_sections),
        absorbCommitMap=_load_section(raw, "absorbCommitMap", _JSON_OBJECT_MAP, invalid_sections),
        mergedPrCreditMap=_load_section(raw, "mergedPrCreditMap", _JSON_OBJECT_MAP, invalid_sections),
        absorbedCreditMap=_load_section(raw, "absorbedCreditMap", _JSON_OBJECT_MAP, invalid_sections),
        shipCommentClassifications=_load_section(raw, "shipCommentClassifications", _JSON_OBJECT_MAP, invalid_sections),
        commitScanMeta=_load_section(raw, "commitScanMeta", _JSON_OBJECT_MAP, invalid_sections),
        invalid_sections=frozenset(invalid_sections),
    )


def _load_section(
    raw: dict[str, object],
    name: str,
    adapter: TypeAdapter[_T],
    invalid_sections: set[str],
) -> _T:
    value = raw.get(name, {})
    try:
        return adapter.validate_python(value)
    except (ValidationError, RecursionError):
        invalid_sections.add(name)
        return adapter.validate_python({})


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
