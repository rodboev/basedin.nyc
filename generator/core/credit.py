from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from core.classify import get_release_tag
from core.leaderboard import is_leaderboard_bot
from core.models import Cache

CreditMap = dict[str, set[int]]
CreditSourceName = str

DEFLECTION_PATTERN = re.compile(
    r"\b(?:supersed(?:e|ed|es|ing)|replaced|covered|duplicat(?:e|ed|es|ing)|consolidat(?:e|ed|es|ing)|closed?\s+in\s+favor|closing\s+in\s+favor)\b",
    re.IGNORECASE,
)
PULL_REQUEST_REF_PATTERN = re.compile(r"(?:https://github\.com/[^/\s]+/[^/\s]+/pull/|#)(\d+)")
OWN_SHIP_PATTERN = re.compile(
    r"\b(?:cherry[- ]?pick(?:ed|ing)?|absorbed|salvaged\s+into|merged[- ]?via|commits?\s+carried|carried\s+forward|included|landed|integrated)\b",
    re.IGNORECASE,
)
PLAIN_SHIP_PATTERN = re.compile(r"\b(?:shipped|released)\b|\bv\d+\.\d+\.\d+", re.IGNORECASE)
CONTRIBUTORS_RANKED_PATTERN = re.compile(r"\|\s*\d+\s*\|\s*\[@([\w-]+)\]\([^)]+\)\s*\|\s*(\d+)\s*\|")

SOURCE_CHANGELOG = "changelog"
SOURCE_COMMIT = "commit"
SOURCE_MERGED = "merged"
SOURCE_ABSORBED = "absorbed"
SOURCE_ABSORB_COMMIT = "absorb-commit"
SOURCE_SHIP_COMMENT = "ship-comment"


@dataclass(frozen=True)
class PullRequestCreditState:
    author_login: str = ""
    state: str = ""
    title: str = ""
    classification: str = ""
    via_url: str = ""
    persisted: bool = True


@dataclass(frozen=True)
class CreditVerificationContext:
    pull_requests: Mapping[int, PullRequestCreditState]
    excluded_logins: frozenset[str] = frozenset()
    release_vehicle_author_pattern: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"(?i)hermes$|nesquena"),
    )

    def get_pr(self, number: int) -> PullRequestCreditState | None:
        return self.pull_requests.get(number)

@dataclass(frozen=True)
class CachedReleaseCreditInputs:
    changelog_map: CreditMap
    commit_map: CreditMap
    merged_map: CreditMap
    absorbed_map: CreditMap
    absorb_commit_map: CreditMap
    ship_comment_map: CreditMap
    context: CreditVerificationContext


def new_empty_credit_map() -> CreditMap:
    return {}


def add_credit_pair(credit_map: CreditMap, login: str, number: int) -> None:
    if not login or number <= 0:
        return
    key = _existing_login_key(credit_map, login)
    credit_map.setdefault(key, set()).add(number)


def normalize_credit_map(raw: Mapping[str, Iterable[int]]) -> CreditMap:
    result: CreditMap = {}
    for login, numbers in raw.items():
        for number in numbers:
            add_credit_pair(result, login, int(number))
    return result


def merge_credit_maps(maps: Iterable[Mapping[str, Iterable[int]]]) -> CreditMap:
    merged = new_empty_credit_map()
    for credit_map in maps:
        for login, numbers in credit_map.items():
            for number in numbers:
                add_credit_pair(merged, login, int(number))
    return merged


def credit_count_map(credit_map: Mapping[str, Iterable[int]]) -> dict[str, int]:
    return {login: len(set(numbers)) for login, numbers in credit_map.items()}


def get_webui_changelog_credit_map(text: str) -> CreditMap:
    credit_map = new_empty_credit_map()
    if not text:
        return credit_map

    for pattern, number_group, login_group in (
        (r"(?im)-\s*\*\*PR #(\d+)\*\* by @([\w-]+)", 1, 2),
        (r"(?im)\*\*PR #(\d+)\*\* by @([\w-]+)", 1, 2),
        (r"(?im)PR #(\d+) by @([\w-]+)", 1, 2),
        (r"(?im)@([\w-]+)\s*[\u2014\u2013-]\s*PR #(\d+)", 2, 1),
        (r"(?im)\(credit:\s*@([\w-]+)\)[^\n]{0,240}?PR #(\d+)", 2, 1),
        (r"(?im)PR #(\d+)[^\n]{0,240}?\(credit:\s*@([\w-]+)\)", 1, 2),
    ):
        for match in re.finditer(pattern, text):
            add_credit_pair(credit_map, match.group(login_group), int(match.group(number_group)))

    for match in re.finditer(r"(?im)\((#[\d\s/]+),\s*@([\w-]+)\)", text):
        login = match.group(2)
        for pr_match in re.finditer(r"#(\d+)", match.group(1)):
            add_credit_pair(credit_map, login, int(pr_match.group(1)))

    return credit_map

def get_contributors_md_ranked_credits(text: str, excluded_logins: Iterable[str] = ()) -> dict[str, int]:
    folded_exclusions = {login.lower() for login in excluded_logins}
    result: dict[str, int] = {}
    for match in CONTRIBUTORS_RANKED_PATTERN.finditer(text):
        login = match.group(1)
        if is_leaderboard_bot(login) or login.lower() in folded_exclusions:
            continue
        if _existing_login_key(result, login) in result:
            continue
        result[login] = int(match.group(2))
    return result


def github_login_from_coauthor_trailer(trailer_line: str) -> str | None:
    if not trailer_line:
        return None
    email_match = re.search(r"([\w-]+(?:\+[\w-]+)?@users\.noreply\.github\.com)", trailer_line, re.IGNORECASE)
    if not email_match:
        return None
    local = email_match.group(1).split("@", 1)[0]
    numeric_prefix = re.match(r"^\d+\+(.+)$", local)
    return numeric_prefix.group(1) if numeric_prefix else local


def invoke_ship_comment_classifier(
    *,
    pr_number: int,
    comment_body: str,
    pr_author_login: str,
    pull_request_author: Callable[[int], str | None],
    coauthor_index: Mapping[int, set[str]] | None = None,
    commit_messages_for_pr: Callable[[int], Iterable[str]] | None = None,
) -> str | None:
    if not comment_body:
        return None

    has_deflection = DEFLECTION_PATTERN.search(comment_body) is not None
    referenced_prs = {
        int(match.group(1))
        for match in PULL_REQUEST_REF_PATTERN.finditer(comment_body)
        if int(match.group(1)) != pr_number
    }

    if has_deflection and referenced_prs:
        folded_author = pr_author_login.casefold()
        if OWN_SHIP_PATTERN.search(comment_body):
            return "own-ship"

        for superseding_pr in referenced_prs:
            superseding_author = pull_request_author(superseding_pr)
            if superseding_author is not None and superseding_author.casefold() == folded_author:
                return "own-ship"
            if coauthor_index and folded_author in {login.casefold() for login in coauthor_index.get(superseding_pr, set())}:
                return "co-author-ship"
            if commit_messages_for_pr is None:
                continue
            for message in commit_messages_for_pr(superseding_pr):
                if re.search(rf"(?i)Co-authored-by:.*{re.escape(pr_author_login)}", message):
                    return "co-author-ship"
        return "deflection"

    if OWN_SHIP_PATTERN.search(comment_body):
        return "own-ship"
    if PLAIN_SHIP_PATTERN.search(comment_body):
        return "plain-ship"
    return None


def best_ship_comment_classification(classifications: Iterable[str | None]) -> str:
    priority_order = {"own-ship": 4, "co-author-ship": 3, "plain-ship": 2, "deflection": 1}
    best: str | None = None
    for classification in classifications:
        if classification is None:
            continue
        if best is None or priority_order[classification] > priority_order[best]:
            best = classification
    return best if best is not None else "none"


def confirm_upstream_release_credit_map(
    *,
    changelog_map: Mapping[str, Iterable[int]],
    commit_map: Mapping[str, Iterable[int]],
    merged_map: Mapping[str, Iterable[int]],
    absorbed_map: Mapping[str, Iterable[int]],
    absorb_commit_map: Mapping[str, Iterable[int]] | None = None,
    ship_comment_map: Mapping[str, Iterable[int]] | None = None,
    context: CreditVerificationContext,
) -> CreditMap:
    absorb_commit_map = absorb_commit_map or {}
    ship_comment_map = ship_comment_map or {}
    source_maps = {
        SOURCE_CHANGELOG: normalize_credit_map(changelog_map),
        SOURCE_COMMIT: normalize_credit_map(commit_map),
        SOURCE_MERGED: normalize_credit_map(merged_map),
        SOURCE_ABSORBED: normalize_credit_map(absorbed_map),
        SOURCE_ABSORB_COMMIT: normalize_credit_map(absorb_commit_map),
        SOURCE_SHIP_COMMENT: normalize_credit_map(ship_comment_map),
    }
    sources = merge_credit_maps(source_maps.values())
    verified = new_empty_credit_map()

    for login, numbers in sources.items():
        for number in numbers:
            pr = context.get_pr(number)
            if pr is None or pr.author_login.lower() != login.lower():
                continue
            if is_vehicle_pull_request(pr, context):
                continue

            source_names = _source_names_for_pair(source_maps, login, number)
            if SOURCE_MERGED in source_names:
                add_credit_pair(verified, login, number)
                continue
            if SOURCE_COMMIT in source_names:
                if pr.state == "MERGED" or pr.classification == "accepted-indirect" or (pr.classification == "shipped" and pr.via_url):
                    add_credit_pair(verified, login, number)
                continue
            if SOURCE_ABSORB_COMMIT in source_names:
                if pr.state == "CLOSED":
                    add_credit_pair(verified, login, number)
                continue
            if SOURCE_SHIP_COMMENT in source_names:
                add_credit_pair(verified, login, number)
                continue
            if SOURCE_ABSORBED in source_names:
                if pr.state == "CLOSED":
                    add_credit_pair(verified, login, number)
                continue
            # PS1 performs a live state lookup here; persisted marks that lookup as available in no-network tests.
            if SOURCE_CHANGELOG in source_names and pr.persisted and pr.state == "MERGED":
                add_credit_pair(verified, login, number)

    return verified

def cached_release_credit_inputs(
    *,
    cache: Cache,
    repo: str,
    changelog_text: str,
    excluded_logins: Iterable[str] = (),
    ship_comment_top: int = 10,
) -> CachedReleaseCreditInputs:
    excluded = tuple(excluded_logins)
    changelog_map = get_webui_changelog_credit_map(changelog_text)
    commit_map = _cached_repo_credit_map(cache.commitCreditMap, repo)
    merged_map = _cached_repo_credit_map(cache.mergedPrCreditMap, repo)
    absorbed_map = _cached_repo_credit_map(cache.absorbedCreditMap, repo)
    absorb_commit_map = _cached_repo_credit_map(cache.absorbCommitMap, repo)
    premerged_map = merge_credit_maps((changelog_map, commit_map, merged_map, absorbed_map, absorb_commit_map))
    scan_logins = _top_credit_logins(premerged_map, ship_comment_top)
    ship_comment_map = _cached_ship_comment_credit_map(
        cache=cache,
        repo=repo,
        scan_logins=scan_logins,
        already_credited_map=premerged_map,
        excluded_logins=excluded,
    )
    context = cached_credit_verification_context(cache=cache, repo=repo, excluded_logins=excluded)
    return CachedReleaseCreditInputs(
        changelog_map=changelog_map,
        commit_map=commit_map,
        merged_map=merged_map,
        absorbed_map=absorbed_map,
        absorb_commit_map=absorb_commit_map,
        ship_comment_map=ship_comment_map,
        context=context,
    )

def cached_release_credit_counts(
    *,
    cache: Cache,
    repo: str,
    changelog_text: str,
    contributors_text: str = "",
    excluded_logins: Iterable[str] = (),
    ship_comment_top: int = 10,
) -> dict[str, int]:
    excluded = tuple(excluded_logins)
    inputs = cached_release_credit_inputs(
        cache=cache,
        repo=repo,
        changelog_text=changelog_text,
        excluded_logins=excluded,
        ship_comment_top=ship_comment_top,
    )
    verified = confirm_upstream_release_credit_map(
        changelog_map=inputs.changelog_map,
        commit_map=inputs.commit_map,
        merged_map=inputs.merged_map,
        absorbed_map=inputs.absorbed_map,
        absorb_commit_map=inputs.absorb_commit_map,
        ship_comment_map=inputs.ship_comment_map,
        context=inputs.context,
    )
    counts = credit_count_map(verified)
    for login, count in get_contributors_md_ranked_credits(contributors_text, excluded).items():
        if _existing_login_key(counts, login) not in counts:
            counts[login] = count
    return counts

def cached_credit_verification_context(
    *,
    cache: Cache,
    repo: str,
    excluded_logins: Iterable[str] = (),
) -> CreditVerificationContext:
    pull_requests: dict[int, PullRequestCreditState] = {}

    for key, raw_state in cache.prPullStates.items():
        number = _repo_pr_number(repo, key)
        if number is None:
            continue
        entry = cache.entries.get(key)
        author_login = _string_value(raw_state.get("author"))
        if not author_login:
            author_login = cache.prAuthorsByNumber.get(key, "")
        pull_requests[number] = PullRequestCreditState(
            author_login=author_login,
            state=_string_value(raw_state.get("state")),
            title=_string_value(raw_state.get("title")),
            classification=entry.classification if entry is not None else "",
            via_url=entry.viaUrl if entry is not None else "",
            persisted=True,
        )

    for key, author_login in cache.prAuthorsByNumber.items():
        number = _repo_pr_number(repo, key)
        if number is None or number in pull_requests:
            continue
        entry = cache.entries.get(key)
        pull_requests[number] = PullRequestCreditState(
            author_login=author_login,
            classification=entry.classification if entry is not None else "",
            via_url=entry.viaUrl if entry is not None else "",
            persisted=False,
        )

    return CreditVerificationContext(
        pull_requests=pull_requests,
        excluded_logins=frozenset(excluded_logins),
    )


def is_vehicle_pull_request(pr: PullRequestCreditState, context: CreditVerificationContext) -> bool:
    if not get_release_tag(pr.title):
        return False
    folded = pr.author_login.lower()
    if is_leaderboard_bot(pr.author_login):
        return True
    if any(login.lower() == folded for login in context.excluded_logins):
        return True
    return context.release_vehicle_author_pattern.search(pr.author_login) is not None


def _source_names_for_pair(source_maps: Mapping[CreditSourceName, CreditMap], login: str, number: int) -> set[CreditSourceName]:
    return {
        source_name
        for source_name, credit_map in source_maps.items()
        if _existing_login_key(credit_map, login) in credit_map and number in credit_map[_existing_login_key(credit_map, login)]
    }


def _existing_login_key(credit_map: Mapping[str, object], login: str) -> str:
    folded = login.lower()
    for existing in credit_map:
        if existing.lower() == folded:
            return existing
    return login

def _cached_repo_credit_map(section: Mapping[str, Mapping[str, object]], repo: str) -> CreditMap:
    entry = section.get(repo)
    if entry is None:
        return {}
    credits = entry.get("credits")
    if not isinstance(credits, Mapping):
        return {}
    result = new_empty_credit_map()
    for login, raw_numbers in credits.items():
        if not isinstance(login, str) or isinstance(raw_numbers, (str, bytes)) or not isinstance(raw_numbers, Iterable):
            continue
        for raw_number in raw_numbers:
            number = _int_value(raw_number)
            if number is not None:
                add_credit_pair(result, login, number)
    return result

def _cached_ship_comment_credit_map(
    *,
    cache: Cache,
    repo: str,
    scan_logins: Iterable[str],
    already_credited_map: Mapping[str, Iterable[int]],
    excluded_logins: Iterable[str],
) -> CreditMap:
    scan_login_set = {login.lower() for login in scan_logins}
    excluded_login_set = {login.lower() for login in excluded_logins}
    result = new_empty_credit_map()
    for key, entry in cache.shipCommentClassifications.items():
        number = _repo_pr_number(repo, key)
        if number is None:
            continue
        classification = _string_value(entry.get("classification"))
        if classification not in {"own-ship", "co-author-ship", "plain-ship"}:
            continue
        author_login = _cached_pr_author(cache, repo, number)
        if not author_login or author_login.lower() not in scan_login_set:
            continue
        if is_leaderboard_bot(author_login) or author_login.lower() in excluded_login_set:
            continue
        existing_key = _existing_login_key(already_credited_map, author_login)
        credited_numbers = already_credited_map.get(existing_key, ())
        if number in {int(raw_number) for raw_number in credited_numbers}:
            continue
        add_credit_pair(result, author_login, number)
    return result

def _top_credit_logins(credit_map: Mapping[str, Iterable[int]], top: int) -> list[str]:
    if top <= 0:
        return []
    return [
        login
        for login, _numbers in sorted(
            credit_map.items(),
            key=lambda item: len(set(item[1])),
            reverse=True,
        )[:top]
    ]

def _cached_pr_author(cache: Cache, repo: str, number: int) -> str:
    key = f"{repo}#{number}"
    state = cache.prPullStates.get(key)
    if state is not None:
        author = _string_value(state.get("author"))
        if author:
            return author
    return cache.prAuthorsByNumber.get(key, "")

def _repo_pr_number(repo: str, key: str) -> int | None:
    prefix = f"{repo}#"
    if not key.startswith(prefix):
        return None
    return _int_value(key[len(prefix):])

def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""

def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
