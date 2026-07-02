from __future__ import annotations

from collections.abc import Callable

import pytest

from core.classify import (
    classify_closed_pr,
    get_pull_request_reference_contexts,
    has_positive_pull_request_reference_context,
    is_author_withdrawn,
    is_credited_merged_sibling_by_maintainer_carry_forward,
    should_resolve_referenced_pull_request,
)
from core.models import Comment, Evidence, PullRequest, PullRequestRef, TimelineEvent


def test_direct_merge_wins_first(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr(state="MERGED", mergedAt="2026-07-01T00:00:00Z")
    evidence = make_evidence(comments=[make_comment(body="superseded by something else")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "direct-merge"
    assert result.via_label == "direct"


def test_positive_release_closer_ships_pr(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    release = make_ref(number=30, title="Release v1.2.3")
    evidence = make_evidence(
        timeline_items=[make_event(closer=release)],
        reference_text_by_pr={30: "Release v1.2.3 ships #10 with credit to @rodboev"},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "timeline"
    assert result.release == "v1.2.3"
    assert result.via_label == "#30"


def test_negative_release_context_does_not_ship_pr(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    release = make_ref(number=30, title="Release v1.2.3")
    evidence = make_evidence(
        comments=[],
        timeline_items=[make_event(closer=release)],
        reference_text_by_pr={30: "Release v1.2.3 closes #10 in favor of #20"},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "withdrawn"


def test_author_withdrawal_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Closing this, withdrawn", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"


def test_accepted_sibling_branch(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    sibling = make_ref(number=22, url="https://github.com/owner/repo/pull/22")
    evidence = make_evidence(
        timeline_items=[make_event(__typename="CrossReferencedEvent", source=sibling)],
        reference_text_by_pr={22: "Merged #22 carries forward #10 by @rodboev"},
        pull_states_by_pr={22: sibling},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "accepted-indirect"
    assert result.evidence_kind == "accepted-indirect"
    assert result.via_label == "#22"


def test_timeline_accepted_sibling_requires_resolved_state(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    sibling = make_ref(number=22, url="https://github.com/owner/repo/pull/22")
    evidence = make_evidence(
        timeline_items=[make_event(__typename="CrossReferencedEvent", source=sibling)],
        reference_text_by_pr={22: "Merged #22 carries forward #10 by @rodboev"},
        pull_states_by_pr={},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"


def test_credited_ship_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Shipped in v1.2.3 via #25 with co-author credit")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "accepted-indirect"
    assert result.via_label == "#25"


def test_superseded_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Closing in favor of the consolidated implementation")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.evidence_kind == "superseded"


def test_duplicate_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="duplicate of another accepted PR", authorAssociation="CONTRIBUTOR")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"
    assert result.log_label == "lost (competing PR won)"


def test_uncredited_superseded_reference_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="superseded by #20", author={"login": "outsider"}, authorAssociation="CONTRIBUTOR")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"
    assert result.log_label == "lost (superseded without maintainer credit)"


def test_comment_shipped_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="This was shipped yesterday", authorAssociation="CONTRIBUTOR")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "comment"


def test_no_comments_withdrawn_branch(make_pr: Callable[..., PullRequest], make_evidence: Callable[..., Evidence]) -> None:
    result = classify_closed_pr(make_pr(), make_evidence())

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "withdrawn"


def test_default_lost_branch(make_pr: Callable[..., PullRequest], make_comment: Callable[..., Comment], make_evidence: Callable[..., Evidence]) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Thanks, but this is stale", authorAssociation="CONTRIBUTOR")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"
    assert result.evidence_kind == "lost"


def test_reference_context_extracts_line_and_radius_context() -> None:
    contexts = get_pull_request_reference_contexts("first\nRelease carries #10 with credit\nlast", "owner/repo", 10)

    assert "Release carries #10 with credit" in contexts
    assert any("#10" in context for context in contexts)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Release ships #10 with credit", True),
        ("Release mentions #10 in favor of #20", False),
        ("Release notes #10 by @rodboev", True),
    ],
)
def test_positive_reference_context_boundary(text: str, expected: bool) -> None:
    assert has_positive_pull_request_reference_context(text, "owner/repo", 10, "rodboev") is expected


def test_author_comment_without_maintainer_interaction_is_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="I will revisit this later", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")])

    assert is_author_withdrawn(pr, evidence)


def test_author_withdrawal_uses_default_author_when_pr_author_missing(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr(author={"login": ""})
    evidence = make_evidence(
        comments=[make_comment(body="Closing this", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")],
        default_author_login="rodboev",
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"


def test_maintainer_carry_forward_requires_commit_author_match(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    merged = make_ref(number=22)
    evidence = make_evidence(
        comments=[make_comment(body="Closing as superseded by #22 with co-author credit merged to main")],
        commit_author_logins_by_pr={22: {"rodboev"}},
    )

    assert is_credited_merged_sibling_by_maintainer_carry_forward("owner/repo", pr, evidence, merged)


def test_maintainer_carry_forward_matches_commit_author_case_insensitively(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr(author={"login": "Michaelyklam"})
    merged = make_ref(number=22)
    evidence = make_evidence(
        comments=[make_comment(body="Closing as superseded by #22 with co-author credit merged to main")],
        commit_author_logins_by_pr={22: {"michaelyklam"}},
    )

    assert is_credited_merged_sibling_by_maintainer_carry_forward("owner/repo", pr, evidence, merged)


def test_maintainer_carry_forward_rejects_missing_commit_author(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    merged = make_ref(number=22)
    evidence = make_evidence(
        comments=[make_comment(body="Closing as superseded by #22 with co-author credit merged to main")],
        commit_author_logins_by_pr={22: {"someone-else"}},
    )

    assert not is_credited_merged_sibling_by_maintainer_carry_forward("owner/repo", pr, evidence, merged)


@pytest.mark.parametrize(
    ("text", "number", "expected"),
    [
        ("see #99", 99, False),
        ("see https://github.com/owner/repo/pull/99", 99, True),
        ("see #100", 100, True),
    ],
)
def test_referenced_pr_resolution_boundary(text: str, number: int, expected: bool) -> None:
    assert should_resolve_referenced_pull_request(text, number) is expected
