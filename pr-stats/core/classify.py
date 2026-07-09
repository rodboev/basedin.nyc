from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from core.models import Comment, Evidence, PullRequest, PullRequestRef, TimelineEvent

MAINTAINER_SHIP_PATTERN = re.compile(
    r"\b(?:shipped|cherry[- ]?pick(?:ed|ing)?|salvaged\s+into|merged[- ]via|merged\s+(?:onto|into|to)\s+(?:main|master|(?:the\s+)?\S+\s+branch)|merged\s+manually|manually\s+merged)\b",
    re.IGNORECASE,
)
DUPLICATE_PATTERNS: tuple[str, ...] = ("duplicate",)
SUPERSEDED_PATTERNS: tuple[str, ...] = ("supersed", "consolidat", "closing in favor", "closed in favor", "closing because")
CREDIT_PATTERNS: tuple[str, ...] = ("co-author", "coauthor", "co-authored", "authorship", "attribution", "credited")
CONTINUATION_PATTERNS: tuple[str, ...] = ("same credit", "same commit", "same change", "reopen")
WITHDRAWN_PATTERN = re.compile(r"\bwithdraw(?:ing|n)?\b", re.IGNORECASE)
AUTHOR_CLOSE_PATTERN = re.compile(r"\bclos(?:ing|ed|e)\b", re.IGNORECASE)
MERGED_CARRY_FORWARD_PATTERN = re.compile(r"\bmerge(?:d|s|ing)?(?:\s+to\s+main)?\b", re.IGNORECASE)
NEGATIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:supersed(?:e|ed|es|ing)|duplicate|dedup-winner\s+over|alternative|chosen\s+over|rather\s+than|instead\s+of|in\s+favor\s+of|builds?\s+on|groundwork\s+from|does\s+not\s+revive|not\s+reviv(?:e|ed|ing)|not\s+included|exclu(?:d(?:e|ed|es|ing)|sion)|sent\s+back|diff\s+didn['’]?t\s+match|needs?\s+(?:rebase|migration\s+plan|[\w-]+\s+validation)|browser-reserved|moot)\b",
    re.IGNORECASE,
)
POSITIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:ship(?:s|ped|ping)?|release(?:d)?|credit(?:ed)?|co-auth(?:or|ored)|authorship|attribution|carry(?:ing|ied)\s+forward|land(?:ed|ing)|merge(?:d|s|ing)?|fix(?:es|ed)?|close(?:s|d)?)\b",
    re.IGNORECASE,
)
MIN_SPECULATIVE_REFERENCED_PR_NUMBER = 100


@dataclass(frozen=True)
class ClassificationResult:
    classification: str
    release: str = ""
    via_label: str = ""
    via_url: str = ""
    evidence_kind: str = "lost"
    from_cache: bool = False
    log_label: str = "lost"


@dataclass(frozen=True)
class CreditedShipEvidence:
    via_number: int = 0


@dataclass(frozen=True)
class SupersededEvidence:
    replacement: PullRequestRef | None = None
    third_party: bool = False


def classify_closed_pr(pr: PullRequest, evidence: Evidence) -> ClassificationResult:
    comments = get_non_bot_comment_text(evidence)
    closed_event = next((item for item in evidence.timeline_items if item.typename == "ClosedEvent"), None)
    merged_release_closer: PullRequestRef | None = None
    if (
        closed_event is not None
        and closed_event.closer is not None
        and closed_event.closer.merged
        and is_release_title(closed_event.closer.title)
        and is_positive_release_reference_to_pull_request(pr.repo, pr, closed_event.closer, evidence)
    ):
        merged_release_closer = closed_event.closer

    release_cross_ref_candidates = [
        item
        for item in evidence.timeline_items
        if item.typename == "CrossReferencedEvent"
        and item.source is not None
        and item.source.merged
        and is_release_title(item.source.title)
    ]
    merged_release_cross_ref_candidate = select_best_cross_reference(
        release_cross_ref_candidates,
        closed_event.createdAt if closed_event is not None else "",
    )
    merged_release_cross_ref = merged_release_cross_ref_candidate
    if merged_release_cross_ref is not None and not is_positive_release_reference_to_pull_request(
        pr.repo,
        pr,
        merged_release_cross_ref,
        evidence,
    ):
        merged_release_cross_ref = None

    release_ref_commit = next(
        (
            item.commit
            for item in evidence.timeline_items
            if item.typename == "ReferencedEvent" and item.commit is not None and is_release_title(item.commit.messageHeadline)
        ),
        None,
    )
    # The release tag is informational and comes from the ungated candidates:
    # a release PR that lists this PR in its changelog names the release even
    # when its reference context is not positive enough to prove shipping.
    release = ""
    for candidate in (
        comments,
        closed_event.closer.title if closed_event is not None and closed_event.closer is not None and closed_event.closer.merged and is_release_title(closed_event.closer.title) else "",
        merged_release_cross_ref_candidate.title if merged_release_cross_ref_candidate is not None else "",
        release_ref_commit.messageHeadline if release_ref_commit is not None else "",
    ):
        release = get_release_tag(candidate)
        if release:
            break

    is_direct_merged = pr.state == "MERGED" or bool(pr.mergedAt)
    is_timeline_shipped = bool(merged_release_closer or merged_release_cross_ref or release_ref_commit)
    ship_comment_body = has_maintainer_ship_comment(pr, evidence)
    is_duplicate = matches_any_pattern(comments, DUPLICATE_PATTERNS)
    superseded_evidence = get_superseded_evidence(pr, evidence)
    has_superseded_ref = has_superseded_reference(pr, evidence)
    is_author_withdrawn_value = is_author_withdrawn(pr, evidence)
    accepted_sibling = get_timeline_credited_merged_pull_request(pr.repo, pr, evidence)
    if accepted_sibling is None and (is_duplicate or superseded_evidence is not None or bool(comments)):
        accepted_sibling = get_referenced_merged_pull_request(pr.repo, pr, evidence, comments)
    credited_ship = get_credited_ship_evidence(pr, evidence)

    if is_direct_merged or is_timeline_shipped:
        if is_direct_merged:
            return ClassificationResult(
                classification="shipped",
                release=release,
                via_label="direct",
                via_url=f"https://github.com/{pr.repo}/pull/{pr.number}",
                evidence_kind="direct-merge",
                log_label="shipped (merged directly)",
            )
        if merged_release_closer is not None:
            return ClassificationResult(
                classification="shipped",
                release=release,
                via_label=f"#{merged_release_closer.number}",
                via_url=merged_release_closer.url,
                evidence_kind="timeline",
                log_label=f"shipped (released via #{merged_release_closer.number})",
            )
        if merged_release_cross_ref is not None:
            return ClassificationResult(
                classification="shipped",
                release=release,
                via_label=f"#{merged_release_cross_ref.number}",
                via_url=merged_release_cross_ref.url,
                evidence_kind="timeline",
                log_label=f"shipped (referenced by merged #{merged_release_cross_ref.number})",
            )
        if release_ref_commit is not None:
            return ClassificationResult(
                classification="shipped",
                release=release,
                via_label=release_ref_commit.oid[:7],
                via_url=release_ref_commit.url,
                evidence_kind="timeline",
                log_label="shipped (referenced by release commit)",
            )
        return ClassificationResult(classification="shipped", release=release, evidence_kind="comment", log_label="shipped")

    if is_author_withdrawn_value:
        return ClassificationResult(classification="withdrawn", release=release, evidence_kind="author-withdrawn", log_label="withdrawn (author withdrew)")
    if accepted_sibling is not None:
        return ClassificationResult(
            classification="accepted-indirect",
            release=release,
            via_label=f"#{accepted_sibling.number}",
            via_url=accepted_sibling.url,
            evidence_kind="accepted-indirect",
            log_label=f"accepted indirectly via #{accepted_sibling.number}",
        )
    if credited_ship is not None:
        via_label = f"#{credited_ship.via_number}" if credited_ship.via_number > 0 else ""
        via_url = f"https://github.com/{pr.repo}/pull/{credited_ship.via_number}" if credited_ship.via_number > 0 else ""
        label_suffix = f" via #{credited_ship.via_number}" if credited_ship.via_number > 0 else ""
        return ClassificationResult(
            classification="accepted-indirect",
            release=release,
            via_label=via_label,
            via_url=via_url,
            evidence_kind="accepted-indirect",
            log_label=f"accepted indirectly{label_suffix} (credited ship)",
        )
    if superseded_evidence is not None:
        replacement = superseded_evidence.replacement
        via_label = f"#{replacement.number}" if replacement is not None else ""
        via_url = replacement.url if replacement is not None else ""
        if superseded_evidence.third_party and replacement is not None:
            return ClassificationResult(
                classification="lost",
                release=release,
                via_label=via_label,
                via_url=via_url,
                evidence_kind="lost",
                log_label=f"lost (competing #{replacement.number} accepted instead)",
            )
        if replacement is not None and (replacement.state == "MERGED" or replacement.mergedAt):
            return ClassificationResult(
                classification="accepted-indirect",
                release=release,
                via_label=via_label,
                via_url=via_url,
                evidence_kind="accepted-indirect",
                log_label=f"accepted indirectly via #{replacement.number}",
            )
        return ClassificationResult(
            classification="superseded",
            release=release,
            via_label=via_label,
            via_url=via_url,
            evidence_kind="superseded",
            log_label="superseded",
        )
    if ship_comment_body:
        ship_via_label, ship_via_url = get_ship_comment_via(ship_comment_body, pr.repo, pr.number)
        return ClassificationResult(
            classification="shipped",
            release=release,
            via_label=ship_via_label,
            via_url=ship_via_url,
            evidence_kind="comment",
            log_label="shipped",
        )
    if is_duplicate:
        return ClassificationResult(classification="lost", release=release, evidence_kind="lost", log_label="lost (competing PR won)")
    if has_superseded_ref:
        return ClassificationResult(classification="lost", release=release, evidence_kind="lost", log_label="lost (superseded without maintainer credit)")
    if not comments or not comments.strip():
        return ClassificationResult(classification="withdrawn", release=release, evidence_kind="withdrawn", log_label="withdrawn (no maintainer interaction)")
    return ClassificationResult(classification="lost", release=release, evidence_kind="lost", log_label="lost")


def get_release_tag(text: str) -> str:
    match = re.search(r"v\d+\.\d+\.\d+", text or "", re.IGNORECASE)
    return match.group(0) if match else ""


def is_release_title(text: str) -> bool:
    return bool(get_release_tag(text))


def matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def get_non_bot_comment_text(evidence: Evidence) -> str:
    return "\n---\n".join(comment.body for comment in evidence.comments if not is_review_bot_login(comment.author.login))


def is_review_bot_login(login: str) -> bool:
    # REST comment payloads report GitHub Apps as "greptile-apps[bot]";
    # GraphQL reports the bare "greptile-apps". Match both.
    return login.removesuffix("[bot]") == "greptile-apps"


def is_maintainer_comment(repo: str, comment: Comment, evidence: Evidence) -> bool:
    del repo
    login = comment.author.login
    if login and (login in evidence.maintainer_logins or login in evidence.integration_bots):
        return True
    return bool(comment.authorAssociation and comment.authorAssociation in {"OWNER", "COLLABORATOR", "MEMBER"})


def has_maintainer_non_bot_comment(pr: PullRequest, evidence: Evidence) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if is_review_bot_login(comment.author.login):
            continue
        if author_login and comment.author.login == author_login:
            continue
        body = comment.body
        if is_maintainer_comment(pr.repo, comment, evidence):
            return True
        if MAINTAINER_SHIP_PATTERN.search(body):
            return True
        if get_release_tag(body):
            return True
        if matches_any_pattern(body, CREDIT_PATTERNS):
            return True
    return False


def has_maintainer_ship_comment(pr: PullRequest, evidence: Evidence) -> str:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if is_review_bot_login(comment.author.login):
            continue
        if author_login and comment.author.login == author_login:
            continue
        if not is_maintainer_comment(pr.repo, comment, evidence):
            continue
        if has_standalone_ship_statement(comment.body):
            return comment.body
    return ""


_COMMIT_REF_PATTERN = re.compile(r"(?:commit\s+|in[:\s]+|Shipped\s+in[:\s]+)`?([0-9a-f]{7,40})`?", re.IGNORECASE)


def get_ship_comment_via(body: str, repo: str, pr_number: int) -> tuple[str, str]:
    for m in re.finditer(r"(?:via|into|in)\s+(?:PR\s+)?#(\d+)", body, re.IGNORECASE):
        num = int(m.group(1))
        if num != pr_number and num >= MIN_SPECULATIVE_REFERENCED_PR_NUMBER:
            return f"#{num}", f"https://github.com/{repo}/pull/{num}"
    for m in re.finditer(r"(?:PR|pull)\s+#(\d+)", body, re.IGNORECASE):
        num = int(m.group(1))
        if num != pr_number and num >= MIN_SPECULATIVE_REFERENCED_PR_NUMBER:
            return f"#{num}", f"https://github.com/{repo}/pull/{num}"
    commit_match = _COMMIT_REF_PATTERN.search(body)
    if commit_match:
        oid = commit_match.group(1)
        return oid[:7], f"https://github.com/{repo}/commit/{oid}"
    return "rebase", f"https://github.com/{repo}/pull/{pr_number}"


def has_standalone_ship_statement(text: str, radius: int = 80) -> bool:
    # The negative gate is scoped to the statement's own context: a long ship
    # comment may carry incidental negative vocabulary in unrelated sentences.
    for match in MAINTAINER_SHIP_PATTERN.finditer(text or ""):
        start = max(0, match.start() - radius)
        window = text[start : match.end() + radius]
        if not NEGATIVE_REFERENCE_PATTERN.search(window):
            return True
    return False


def is_author_withdrawn(pr: PullRequest, evidence: Evidence) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    if not author_login:
        return False
    author_commented = False
    for comment in evidence.comments:
        if comment.author.login != author_login:
            continue
        author_commented = True
        body = comment.body
        if WITHDRAWN_PATTERN.search(body):
            return True
        if matches_any_pattern(body, SUPERSEDED_PATTERNS):
            return True
        if AUTHOR_CLOSE_PATTERN.search(body):
            return True
    return author_commented and not has_maintainer_non_bot_comment(pr, evidence)


def get_superseded_evidence(pr: PullRequest, evidence: Evidence) -> SupersededEvidence | None:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if author_login and comment.author.login == author_login:
            continue
        if not is_maintainer_comment(pr.repo, comment, evidence):
            continue
        body = comment.body
        if not (matches_any_pattern(body, SUPERSEDED_PATTERNS) and not matches_any_pattern(body, CONTINUATION_PATTERNS)):
            continue
        replacement = get_replacement_pull_request(pr, evidence, body)
        third_party = replacement is not None and is_third_party_login(replacement.author.login, author_login, evidence)
        return SupersededEvidence(replacement=replacement, third_party=third_party)
    return None


def get_replacement_pull_request(pr: PullRequest, evidence: Evidence, body: str) -> PullRequestRef | None:
    for match in re.finditer(r"#(\d+)", body):
        number = int(match.group(1))
        if number == pr.number or not should_resolve_referenced_pull_request(body, number):
            continue
        referenced = evidence.pull_states_by_pr.get(number)
        if referenced is not None:
            return referenced
    return None


def is_third_party_login(login: str, original_author: str, evidence: Evidence) -> bool:
    # Methodology: a replacement authored by a competing contributor is a loss;
    # a maintainer's own replacement (or the author's resubmit) is a supersession.
    if not login:
        return False
    if login.casefold() == (original_author or "").casefold():
        return False
    privileged = {name.casefold() for name in (*evidence.maintainer_logins, *evidence.integration_bots)}
    return login.casefold() not in privileged


def has_superseded_reference(pr: PullRequest, evidence: Evidence) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if author_login and comment.author.login == author_login:
            continue
        body = comment.body
        if matches_any_pattern(body, SUPERSEDED_PATTERNS) and not matches_any_pattern(body, CONTINUATION_PATTERNS):
            return True
    return False


def get_credited_ship_evidence(pr: PullRequest, evidence: Evidence) -> CreditedShipEvidence | None:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if author_login and comment.author.login == author_login:
            continue
        if not is_maintainer_comment(pr.repo, comment, evidence):
            continue
        body = comment.body
        if not body:
            continue
        shipped = bool(get_release_tag(body)) or bool(MAINTAINER_SHIP_PATTERN.search(body))
        if not shipped:
            continue
        if not matches_any_pattern(body, CREDIT_PATTERNS):
            continue
        via_match = re.search(r"\b(?:via|by|into|in)\s+#(\d+)", body, re.IGNORECASE)
        return CreditedShipEvidence(via_number=int(via_match.group(1)) if via_match else 0)
    return None


def get_pull_request_reference_contexts(text: str, repo: str, number: int, radius: int = 80) -> list[str]:
    if not text:
        return []
    repo_pattern = re.escape(repo)
    pattern = re.compile(rf"(https://github\.com/{repo_pattern}/pull/{number}\b|(?<![\w/])#{number}\b)", re.IGNORECASE)
    contexts: list[str] = []
    for line in re.split(r"\r?\n", text):
        if pattern.search(line):
            contexts.append(line)
    for match in pattern.finditer(text):
        start = max(0, match.start() - radius)
        end = min(len(text), match.end() + radius)
        contexts.append(text[start:end])
    return list(dict.fromkeys(contexts))


def has_positive_pull_request_reference_context(text: str, repo: str, number: int, author_login: str) -> bool:
    # A reference is positive unless its surrounding context carries explicitly
    # negative vocabulary (superseded, instead of, ...). Release notes routinely
    # list constituent PRs without a shipping verb, and per the methodology a
    # release does not have to link back to the PR to prove the work shipped.
    del author_login
    for context in get_pull_request_reference_contexts(text, repo, number):
        if NEGATIVE_REFERENCE_PATTERN.search(context):
            continue
        return True
    return False


def is_positive_release_reference_to_pull_request(
    repo: str,
    original_pr: PullRequest,
    release_pr: PullRequestRef,
    evidence: Evidence,
) -> bool:
    reference_text = evidence.reference_text_by_pr.get(release_pr.number, "")
    if not reference_text:
        return False
    return has_positive_pull_request_reference_context(reference_text, repo, original_pr.number, original_pr.author.login)


def has_maintainer_credited_reference_to_pull_request(
    repo: str,
    original_pr: PullRequest,
    evidence: Evidence,
    merged_pr_number: int,
) -> bool:
    author_login = author_login_for_classification(original_pr, evidence)
    for comment in evidence.comments:
        if author_login and comment.author.login == author_login:
            continue
        if not is_maintainer_comment(repo, comment, evidence):
            continue
        body = comment.body
        if not body:
            continue
        if not (re.search(rf"(?:^|[^\w])#{merged_pr_number}(?:[^\w]|$)", body, re.IGNORECASE) or is_explicit_pull_request_reference(body, merged_pr_number)):
            continue
        if not matches_any_pattern(body, CREDIT_PATTERNS):
            continue
        if (
            matches_any_pattern(body, SUPERSEDED_PATTERNS)
            or matches_any_pattern(body, CONTINUATION_PATTERNS)
            or MERGED_CARRY_FORWARD_PATTERN.search(body)
        ):
            return True
    return False


def is_credited_merged_sibling_by_maintainer_carry_forward(
    repo: str,
    original_pr: PullRequest,
    evidence: Evidence,
    merged_pr: PullRequestRef,
) -> bool:
    if not has_maintainer_credited_reference_to_pull_request(repo, original_pr, evidence, merged_pr.number):
        return False
    original_login = original_pr.author.login
    if not original_login:
        return False
    return original_login.casefold() in {login.casefold() for login in evidence.commit_author_logins_by_pr.get(merged_pr.number, set())}


def is_credited_merged_sibling(repo: str, original_pr: PullRequest, merged_pr: PullRequestRef, evidence: Evidence) -> bool:
    reference_text = evidence.reference_text_by_pr.get(merged_pr.number, "")
    if reference_text and has_positive_pull_request_reference_context(reference_text, repo, original_pr.number, original_pr.author.login):
        return True
    return is_credited_merged_sibling_by_maintainer_carry_forward(repo, original_pr, evidence, merged_pr)


def get_referenced_merged_pull_request(
    repo: str,
    original_pr: PullRequest,
    evidence: Evidence,
    text: str,
) -> PullRequestRef | None:
    if not text:
        return None
    seen: set[int] = set()
    for match in re.finditer(r"#(\d+)", text):
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        if not should_resolve_referenced_pull_request(text, number):
            continue
        referenced_pr = evidence.pull_states_by_pr.get(number)
        if referenced_pr is None:
            continue
        if (referenced_pr.state == "MERGED" or referenced_pr.mergedAt) and is_credited_merged_sibling(repo, original_pr, referenced_pr, evidence):
            return referenced_pr
    return None


def get_timeline_credited_merged_pull_request(repo: str, original_pr: PullRequest, evidence: Evidence) -> PullRequestRef | None:
    for item in evidence.timeline_items:
        if item.typename != "CrossReferencedEvent" or item.source is None:
            continue
        source = item.source
        if not source.merged and not source.mergedAt:
            continue
        merged_pr = evidence.pull_states_by_pr.get(source.number)
        if merged_pr is not None and is_credited_merged_sibling(repo, original_pr, merged_pr, evidence):
            return merged_pr
    return None


def is_explicit_pull_request_reference(text: str, number: int) -> bool:
    return bool(re.search(rf"github\.com/[^/\s]+/[^/\s]+/pull/{number}\b", text))


def should_resolve_referenced_pull_request(text: str, number: int) -> bool:
    if number >= MIN_SPECULATIVE_REFERENCED_PR_NUMBER:
        return True
    return is_explicit_pull_request_reference(text, number)


def select_best_cross_reference(candidates: list[TimelineEvent], closed_at: str) -> PullRequestRef | None:
    if not candidates:
        return None
    if not closed_at:
        return candidates[-1].source
    closed_at_date = _parse_datetime(closed_at)
    if closed_at_date is None:
        return candidates[-1].source

    def distance(candidate: TimelineEvent) -> float:
        created_at = _parse_datetime(candidate.createdAt)
        if created_at is None:
            return float("inf")
        return abs((closed_at_date - created_at).total_seconds())

    return min(candidates, key=distance).source


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def author_login_for_classification(pr: PullRequest, evidence: Evidence) -> str:
    return pr.author.login or evidence.default_author_login
