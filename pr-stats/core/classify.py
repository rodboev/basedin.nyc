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
# Maintainer language crediting the author's design/approach as adopted into a
# named merged PR, even without a co-author trailer on that PR.
ADOPTION_CREDIT_PATTERN = re.compile(
    r"\b(?:you\s+proposed|design\s+carried\s+through|carried\s+through\s+to\s+the\s+final|based\s+on\s+your|your\s+(?:approach|design|implementation|proposal|opt-in))\b",
    re.IGNORECASE,
)
WITHDRAWN_PATTERN = re.compile(r"\bwithdraw(?:ing|n)?\b", re.IGNORECASE)
AUTHOR_CLOSE_PATTERN = re.compile(r"\bclos(?:ing|ed|e)\b", re.IGNORECASE)
MERGED_CARRY_FORWARD_PATTERN = re.compile(r"\bmerge(?:d|s|ing)?(?:\s+to\s+main)?\b", re.IGNORECASE)
NEGATIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:supersed(?:e|ed|es|ing)|duplicate|dedup-winner\s+over|alternative|chosen\s+over|rather\s+than|instead\s+of|in\s+favor\s+of|builds?\s+on|groundwork\s+from|does\s+not\s+revive|not\s+reviv(?:e|ed|ing)|not\s+included|exclu(?:d(?:e|ed|es|ing)|sion)|sent\s+back|diff\s+didn['’]?t\s+match|needs?\s+(?:rebase|migration\s+plan|[\w-]+\s+validation)|browser-reserved|moot)\b",
    re.IGNORECASE,
)
POSITIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:ship(?:s|ped|ping)?|release(?:d)?|credit(?:ed)?|co-auth(?:or|ored)|authorship|attribution|carry(?:ing|ied)\s+forward|land(?:ed|ing)|merge(?:d|s|ing)?|fix(?:es|ed)?|close(?:s|d)?|includ(?:e|es|ed|ing)|incorporat(?:e|es|ed|ing)|subsum(?:e|es|ed|ing)|absorb(?:s|ed|ing)?|folds?\s+in)\b",
    re.IGNORECASE,
)
# Attribution vocabulary a replacement PR's body uses when naming the original
# author at the reference to their PR ("Credit: builds on @author's approach in #N").
AUTHOR_ATTRIBUTION_PATTERN = re.compile(
    r"\b(?:credit(?:s|ed)?|based\s+on|builds?\s+on|thanks\s+to|approach|propos(?:ed|al)|co-auth(?:or|ored)|original(?:ly)?)\b",
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
class AdoptionCredit:
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
    adoption_credit = get_adoption_credit(pr, evidence)
    credited_replacement = get_credited_replacement(pr, evidence)
    if (
        accepted_sibling is not None
        and superseded_evidence is not None
        and superseded_evidence.replacement is not None
        and superseded_evidence.replacement.number == accepted_sibling.number
        and not has_landing_credit(pr, evidence, accepted_sibling)
    ):
        # A maintainer supersession names this merged sibling, but nothing shows the
        # author's content landed there (no co-authorship, ship, or credit comment);
        # the sibling's own "combines/extends" self-description is not credit.
        accepted_sibling = None

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

    if credited_replacement is not None:
        # A merged replacement whose own body credits the author (@author named with
        # attribution vocabulary at the reference to this PR) is an indirect ship,
        # and it outranks the author's close: closing in favor of a credited
        # takeover is handing the work off, not withdrawing it.
        return ClassificationResult(
            classification="accepted-indirect",
            release=release,
            via_label=f"#{credited_replacement.number}",
            via_url=credited_replacement.url,
            evidence_kind="accepted-indirect",
            log_label=f"accepted indirectly via #{credited_replacement.number} (replacement credits author)",
        )
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
    if adoption_credit is not None:
        return ClassificationResult(
            classification="accepted-indirect",
            release=release,
            via_label=f"#{adoption_credit.via_number}",
            via_url=f"https://github.com/{pr.repo}/pull/{adoption_credit.via_number}",
            evidence_kind="accepted-indirect",
            log_label=f"accepted indirectly via #{adoption_credit.via_number} (design adopted)",
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
        if (
            replacement is not None
            and (replacement.state == "MERGED" or replacement.mergedAt)
            and has_landing_credit(pr, evidence, replacement)
        ):
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
    if is_author_closed(pr, evidence):
        # Nothing above found a competitor, a replacement, or a maintainer verdict, so
        # the only decision on record is the author's own close: a withdrawal, not a loss.
        return ClassificationResult(classification="withdrawn", release=release, evidence_kind="author-withdrawn", log_label="withdrawn (author closed it)")
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


SHIPPED_ADJECTIVE_PATTERN = re.compile(
    r"\bshipped\s+(?:artifact|artifacts|build|builds|bundle|bundles|binary|binaries|release|releases|version|versions|code|dist|output|outputs|package|packages)\b",
    re.IGNORECASE,
)


def has_standalone_ship_statement(text: str, radius: int = 80) -> bool:
    # The negative gate is scoped to the statement's own context: a long ship
    # comment may carry incidental negative vocabulary in unrelated sentences.
    for match in MAINTAINER_SHIP_PATTERN.finditer(text or ""):
        # "shipped artifact/bundle/..." uses "shipped" as an adjective, not a ship verb.
        if match.group(0).lower() == "shipped" and SHIPPED_ADJECTIVE_PATTERN.match(text, match.start()):
            continue
        start = max(0, match.start() - radius)
        window = text[start : match.end() + radius]
        if not NEGATIVE_REFERENCE_PATTERN.search(window):
            return True
    return False


def pr_closer_login(evidence: Evidence) -> str:
    for item in reversed(evidence.timeline_items):
        if item.typename == "ClosedEvent" and item.actor is not None and item.actor.login:
            return item.actor.login
    return ""


def is_author_closed(pr: PullRequest, evidence: Evidence) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    closer_login = pr_closer_login(evidence)
    return bool(author_login) and closer_login.casefold() == author_login.casefold()


def is_author_withdrawn(pr: PullRequest, evidence: Evidence) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    if not author_login:
        return False
    # A withdrawal is the author closing their own PR. When the recorded closer is
    # someone else, the close was a maintainer decision (supersede, reject), so the
    # author's own "closing"/"closed" phrasing must not read as a withdrawal.
    closer_login = pr_closer_login(evidence)
    if closer_login and closer_login.casefold() != author_login.casefold():
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
        # A replacement whose author acts as a maintainer on this PR (e.g. the account
        # that filed the "superseded by" close) is a maintainer consolidation, not a
        # competing third-party contributor, even if it is absent from the Members block.
        third_party = (
            replacement is not None
            and is_third_party_login(replacement.author.login, author_login, evidence)
            and replacement.author.login.casefold() not in maintainer_comment_authors(pr, evidence)
        )
        return SupersededEvidence(replacement=replacement, third_party=third_party)
    return None


def maintainer_comment_authors(pr: PullRequest, evidence: Evidence) -> set[str]:
    author_login = author_login_for_classification(pr, evidence)
    logins: set[str] = set()
    for comment in evidence.comments:
        if is_review_bot_login(comment.author.login):
            continue
        if author_login and comment.author.login == author_login:
            continue
        if is_maintainer_comment(pr.repo, comment, evidence):
            logins.add(comment.author.login.casefold())
    return logins


def author_has_commit_credit(pr: PullRequest, evidence: Evidence, number: int) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    if not author_login:
        return False
    logins = {login.casefold() for login in evidence.commit_author_logins_by_pr.get(number, set())}
    return author_login.casefold() in logins


def has_landing_credit(pr: PullRequest, evidence: Evidence, replacement: PullRequestRef) -> bool:
    # Evidence that the author's content actually landed in the replacement: a
    # co-author trailer, a maintainer ship comment about this PR, or an explicit
    # credit comment. A ship statement about the replacement PR itself shipping is
    # expected in every supersession and is not evidence the author's work landed.
    if author_has_commit_credit(pr, evidence, replacement.number):
        return True
    if maintainer_ship_comment_credits_author(pr, evidence, replacement.number):
        return True
    return get_credited_ship_evidence(pr, evidence) is not None


def maintainer_ship_comment_credits_author(pr: PullRequest, evidence: Evidence, replacement_number: int, radius: int = 80) -> bool:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if is_review_bot_login(comment.author.login):
            continue
        if author_login and comment.author.login == author_login:
            continue
        if not is_maintainer_comment(pr.repo, comment, evidence):
            continue
        body = comment.body or ""
        for match in MAINTAINER_SHIP_PATTERN.finditer(body):
            window = body[max(0, match.start() - radius) : match.end() + radius]
            if NEGATIVE_REFERENCE_PATTERN.search(window):
                continue
            numbers = {int(m.group(1)) for m in re.finditer(r"#(\d+)", window)}
            # A ship statement whose only PR reference is the replacement credits the
            # replacement, not this PR; require it to name this PR or no competitor.
            if replacement_number in numbers and pr.number not in numbers:
                continue
            return True
    return False


def get_adoption_credit(pr: PullRequest, evidence: Evidence) -> AdoptionCredit | None:
    author_login = author_login_for_classification(pr, evidence)
    for comment in evidence.comments:
        if is_review_bot_login(comment.author.login):
            continue
        if author_login and comment.author.login == author_login:
            continue
        if not is_maintainer_comment(pr.repo, comment, evidence):
            continue
        body = comment.body or ""
        if not ADOPTION_CREDIT_PATTERN.search(body):
            continue
        via_number = _adoption_via_number(pr, evidence, body)
        if via_number:
            return AdoptionCredit(via_number=via_number)
    return None


def _adoption_via_number(pr: PullRequest, evidence: Evidence, body: str) -> int:
    def is_merged(number: int) -> bool:
        ref = evidence.pull_states_by_pr.get(number)
        return ref is not None and (ref.state == "MERGED" or bool(ref.mergedAt))

    # Credit the merged PR named closest to an adoption phrase; a supersession comment
    # can also name the PR that merely made this one moot, which is not the credit.
    adoptions = list(ADOPTION_CREDIT_PATTERN.finditer(body))
    if adoptions:
        best_number = 0
        best_distance = -1
        for match in re.finditer(r"#(\d+)", body):
            number = int(match.group(1))
            if number == pr.number or not is_merged(number):
                continue
            distance = min(abs(match.start() - adoption.start()) for adoption in adoptions)
            if best_distance < 0 or distance < best_distance:
                best_number, best_distance = number, distance
        if best_number:
            return best_number
    for match in re.finditer(r"#(\d+)", body):
        number = int(match.group(1))
        if number != pr.number and is_merged(number):
            return number
    return 0


def get_credited_replacement(pr: PullRequest, evidence: Evidence) -> PullRequestRef | None:
    # Body credit only. Commit authorship in the replacement is deliberately not
    # evidence here: a release PR shipping a different PR by the same author carries
    # his commits and may mention this PR in passing, which reads as credit for the
    # wrong PR (hermes-webui#5845 shipping #5823 while name-dropping #5597).
    author_login = author_login_for_classification(pr, evidence)
    if not author_login:
        return None
    folded_author = author_login.casefold()
    for number in sorted(evidence.pull_states_by_pr):
        ref = evidence.pull_states_by_pr[number]
        if number == pr.number or not (ref.state == "MERGED" or ref.mergedAt):
            continue
        # The author's own merged PR is a direct ship in its own right; counting it
        # here would double-count the same work.
        if ref.author.login.casefold() == folded_author:
            continue
        if replacement_body_credits_author(ref.body, pr.repo, pr.number, author_login):
            return ref
    return None


def replacement_body_credits_author(body: str, repo: str, number: int, author_login: str) -> bool:
    # Credit must sit at the reference site: @author named in the same context as the
    # link to their PR, with attribution vocabulary. A body that references the PR
    # without naming the author ("supersedes the narrower path in #N") is not credit.
    if not body or not author_login:
        return False
    mention = re.compile(rf"(?<!\w)@{re.escape(author_login)}(?![\w-])", re.IGNORECASE)
    for context in get_pull_request_reference_contexts(body, repo, number):
        if mention.search(context) and AUTHOR_ATTRIBUTION_PATTERN.search(context):
            return True
    return False


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


def has_credited_pull_request_reference_context(text: str, repo: str, number: int) -> bool:
    # A merged sibling credits this PR when its reference to this PR says the work
    # shipped, merged, landed, or was credited. A bare mention ("#N owns adjacent
    # work") names the PR without claiming its content.
    for context in get_pull_request_reference_contexts(text, repo, number):
        if NEGATIVE_REFERENCE_PATTERN.search(context):
            continue
        if POSITIVE_REFERENCE_PATTERN.search(context):
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
    if reference_text and has_credited_pull_request_reference_context(reference_text, repo, original_pr.number):
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
