from __future__ import annotations

from collections.abc import Callable

import pytest

from core.classify import (
    classify_closed_pr,
    get_pull_request_reference_contexts,
    get_ship_comment_via,
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


def test_non_author_close_is_not_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # The author says "I closed both points from the review" (matches the close pattern),
    # but a maintainer closed the PR as superseded; the close phrasing is not a withdrawal.
    pr = make_pr()
    evidence = make_evidence(
        comments=[
            make_comment(body="I closed both points from the review", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR"),
            make_comment(body="Re-closing as superseded by the broader fix", author={"login": "maintainer"}, authorAssociation="COLLABORATOR"),
        ],
        timeline_items=[make_event(actor={"login": "maintainer"})],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"


def test_silent_author_close_over_maintainer_comment_is_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#4866: the author closed his own PR without commenting, leaving a
    # maintainer review that names no competitor and no replacement. Nothing lost.
    pr = make_pr()
    evidence = make_evidence(
        comments=[make_comment(body="Full suite green, no regression risks found. Rebase onto master before landing.", author={"login": "maintainer"}, authorAssociation="COLLABORATOR")],
        timeline_items=[make_event(actor={"login": "rodboev"})],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"


@pytest.mark.parametrize("body", [None, "Thanks for the PR", "Duplicate of #22", "Shipped in v1.2.3", "Superseded by #22", "Closing this PR"])
@pytest.mark.parametrize("comment_author", ["rodboev", "maintainer", "roborev-ci[bot]"])
def test_author_close_is_withdrawn_regardless_of_comments(
    body: str | None,
    comment_author: str,
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    release = make_ref(number=22, title="Release v1.2.3")
    evidence = make_evidence(
        comments=[] if body is None else [make_comment(body=body, author={"login": comment_author})],
        timeline_items=[make_event(actor={"login": "maintainer"}), make_event(actor={"login": "RODBOEV"}, closer=release)],
        reference_text_by_pr={22: "Shipped #10 in v1.2.3"},
        pull_states_by_pr={22: release},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"
    assert result.via_label == ""


def test_latest_maintainer_close_continues_classification(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    evidence = make_evidence(
        comments=[make_comment(body="Duplicate of the work already merged")],
        timeline_items=[make_event(actor={"login": "rodboev"}), make_event(actor={"login": "maintainer"})],
    )

    assert classify_closed_pr(make_pr(), evidence).classification == "lost"


def test_author_close_with_competitor_is_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(
        comments=[make_comment(body="Duplicate of the work already merged", author={"login": "maintainer"}, authorAssociation="COLLABORATOR")],
        timeline_items=[make_event(actor={"login": "rodboev"})],
    )

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


def test_bare_scope_mention_in_merged_sibling_is_not_credit(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # claude-mem#2904: a merged PR's scope section named this PR as adjacent open
    # work. The mention claims nothing about this PR's content, and the negative
    # "excludes" sits too far from the link for the windowed context to see it.
    pr = make_pr()
    sibling = make_ref(number=22, url="https://github.com/owner/repo/pull/22")
    evidence = make_evidence(
        timeline_items=[make_event(__typename="CrossReferencedEvent", source=sibling), make_event(actor={"login": "rodboev"})],
        reference_text_by_pr={
            22: (
                "This excludes retention, pruning, migrations, prompt rewriting, fuzzy classification, "
                "and cleanup of existing rows. Open PRs #10 and #11 own adjacent storage-reclamation work."
            ),
        },
        pull_states_by_pr={22: sibling},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"


def test_merged_sibling_that_absorbs_this_pr_is_credit(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    sibling = make_ref(number=22, url="https://github.com/owner/repo/pull/22")
    evidence = make_evidence(
        timeline_items=[make_event(__typename="CrossReferencedEvent", source=sibling)],
        reference_text_by_pr={22: "This stack folds in #10 and #11 under one review target."},
        pull_states_by_pr={22: sibling},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "accepted-indirect"
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


def test_shipped_comment_beats_incidental_duplicate_wording(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(
        comments=[
            make_comment(body="This test looks duplicate-adjacent", authorAssociation="CONTRIBUTOR"),
            make_comment(body="Shipped in v1.2.3, thank you @rodboev", authorAssociation="COLLABORATOR"),
        ],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "comment"


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


def test_maintainer_merged_via_rebase_comment_ships(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(
        comments=[
            make_comment(body="@greptileai review", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR"),
            make_comment(body="Merged via rebase onto main. Build clean, no regressions.", author={"login": "owner"}, authorAssociation="OWNER"),
        ],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "comment"
    assert result.via_label == "rebase"


def test_comment_shipped_via_pr_reference(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(
        comments=[make_comment(body="Merged via PR #3601, rebased onto current main.")],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.via_label == "#3601"
    assert "/pull/3601" in result.via_url


def test_comment_shipped_via_commit_reference(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(
        comments=[make_comment(body="Shipped in commit `b750c720` on main.")],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.via_label == "b750c72"
    assert "/commit/b750c720" in result.via_url


def test_maintainer_branch_merge_comment_ships(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # thedotmack/claude-mem#3018: open PRs folded into the community-edge release line
    pr = make_pr()
    evidence = make_evidence(
        comments=[
            make_comment(
                body=(
                    "Merged into the `community-edge` branch — claude-mem's bleeding-edge release line. "
                    "Closing here since your change now lives on that branch. Thanks for the contribution!"
                ),
                author={"login": "owner"},
                authorAssociation="OWNER",
            ),
        ],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "comment"


def test_non_maintainer_ship_claim_does_not_ship(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Looks like this was cherry-picked to main", author={"login": "outsider"}, authorAssociation="NONE")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"


def test_author_ship_claim_does_not_ship(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="This shipped in the latest release", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"


def test_maintainer_ship_statement_attributing_other_pr_does_not_ship(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Merged #22 into main instead.")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"


def test_shipped_as_adjective_does_not_ship(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # claude-mem#2849: the maintainer rejects the fix as dead code; "shipped" appears
    # only as an adjective in "shipped artifact", which is not a ship statement.
    pr = make_pr()
    evidence = make_evidence(
        comments=[
            make_comment(
                body="The patched classes are tree-shaken out of every shipped artifact, so this never runs.",
                author={"login": "maintainer"},
                authorAssociation="OWNER",
            ),
        ],
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"


def test_maintainer_negative_context_merge_comment_does_not_ship(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Merged the alternative implementation into main rather than this.")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"


def test_maintainer_superseding_present_tense_is_superseded(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#1821: "superseding" lacks the final 'e' of the "supersede" substring.
    # The replacement PR is unresolvable here, so no via and no third-party call.
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Thanks — superseding this PR with #22 by @other, which covers more of the editor surface.")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.evidence_kind == "superseded"
    assert result.via_label == ""


def test_supersession_by_competing_contributor_is_lost(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#4280: maintainer says "superseded by", but the replacement is a
    # competing contributor's PR — the methodology calls that a loss.
    pr = make_pr()
    replacement = make_ref(number=4285, state="CLOSED", merged=False, mergedAt="", author={"login": "b3nw"}, url="https://github.com/owner/repo/pull/4285")
    evidence = make_evidence(
        comments=[make_comment(body="Superseded by #4285 (b3nw), which fixes the same root cause (#478) and shipped in v0.51.450.")],
        pull_states_by_pr={4285: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"
    assert result.via_label == "#4285"
    assert result.via_url == "https://github.com/owner/repo/pull/4285"


def test_supersession_by_maintainer_replacement_stays_superseded(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    replacement = make_ref(number=4330, state="CLOSED", merged=False, mergedAt="", author={"login": "maintainer"}, url="https://github.com/owner/repo/pull/4330")
    evidence = make_evidence(
        comments=[make_comment(body="Superseded by my #4330 which lands the same fix from the maintainer branch.")],
        pull_states_by_pr={4330: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.via_label == "#4330"


def test_maintainer_independent_merged_replacement_without_credit_is_superseded(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # orca#7874/#7875: the maintainer account closes the PR as superseded by its own
    # merged consolidation. No co-author trailer, ship, or credit comment, and the
    # replacement's self-description referencing this PR is not credit -> superseded.
    pr = make_pr()
    replacement = make_ref(
        number=8242,
        author={"login": "orcawin"},
        url="https://github.com/owner/repo/pull/8242",
    )
    evidence = make_evidence(
        comments=[
            make_comment(
                body="Closing as superseded by #8242, now merged. Thanks for the work and focused tests here.",
                author={"login": "orcawin"},
                authorAssociation="MEMBER",
            ),
        ],
        pull_states_by_pr={8242: replacement},
        reference_text_by_pr={8242: "This combines and extends the narrower approaches in #10."},
        commit_author_logins_by_pr={8242: {"orcawin"}},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.via_label == "#8242"


def test_maintainer_merged_replacement_with_coauthor_credit_is_accepted_indirect(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # orca#6362: same supersession shape, but the author is a co-author on the merged
    # replacement, so the content demonstrably landed -> accepted-indirect.
    pr = make_pr()
    replacement = make_ref(number=6574, author={"login": "maintainer"}, url="https://github.com/owner/repo/pull/6574")
    evidence = make_evidence(
        comments=[make_comment(body="Closing this one as superseded by #6574, but the implementation here was yours.")],
        pull_states_by_pr={6574: replacement},
        commit_author_logins_by_pr={6574: {"rodboev"}},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "accepted-indirect"
    assert result.via_label == "#6574"


def test_replacement_ship_comment_is_not_landing_credit(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#5996: the replacement's own text references this PR positively and a
    # maintainer notes the replacement shipped, but that ship credits the replacement,
    # not this PR's content -> superseded, not accepted-indirect.
    pr = make_pr()
    replacement = make_ref(number=220, author={"login": "maintainer"}, url="https://github.com/owner/repo/pull/220")
    evidence = make_evidence(
        comments=[
            make_comment(body="Closing as superseded by #220."),
            make_comment(body="For context, #220 just shipped in v1.2.3."),
        ],
        pull_states_by_pr={220: replacement},
        reference_text_by_pr={220: "This fixes a different arm than #10. Both needed."},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.via_label == "#220"


def test_ship_comment_crediting_this_pr_is_landing_credit(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # A ship statement that names this PR alongside the replacement is genuine landing
    # credit -> accepted-indirect.
    pr = make_pr()
    replacement = make_ref(number=220, author={"login": "maintainer"}, url="https://github.com/owner/repo/pull/220")
    evidence = make_evidence(
        comments=[
            make_comment(body="Closing as superseded by #220."),
            make_comment(body="Cherry-picked your #10 changes into #220 and shipped in v1.2.3."),
        ],
        pull_states_by_pr={220: replacement},
        reference_text_by_pr={220: "This fixes a different arm than #10. Both needed."},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "accepted-indirect"
    assert result.via_label == "#220"


def test_supersession_by_author_resubmit_stays_superseded(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    replacement = make_ref(number=4400, state="CLOSED", merged=False, mergedAt="", author={"login": "rodboev"}, url="https://github.com/owner/repo/pull/4400")
    evidence = make_evidence(
        comments=[make_comment(body="Superseded by your split #4400, closing this one.")],
        pull_states_by_pr={4400: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.via_label == "#4400"


def test_ship_statement_survives_distant_negative_wording(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#3444: "instead of" in an unrelated sentence must not veto the ship
    body = (
        "Shipped in **v0.51.223** ✅ — cherry-picked onto release stage-p5 (the reworked first-class-provider version). "
        "I also added the env-detection fix a review caught: OPENAI_API_KEY detection now surfaces "
        "openai-api instead of a bare openai the agent registry can't resolve, so env-only setups work end to end. "
        "Thanks @rodboev! Closing as merged-via-release. (Closes #3443.)"
    )
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body=body)])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.release == "v0.51.223"


def test_ship_statement_with_companion_pr_reference_ships(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#4056: a companion PR in the ship sentence is not attribution elsewhere
    pr = make_pr()
    evidence = make_evidence(comments=[make_comment(body="Shipped in **v0.51.410 (Release NW)** ✅ — deployed and live (combined with #4075). Thanks @rodboev.")])

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.release == "v0.51.410"


def test_review_bot_app_login_comments_are_ignored(
    make_pr: Callable[..., PullRequest],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    # REST reports the review bot as "greptile-apps[bot]"; its summaries say
    # "duplicate" freely and must not drive lost classifications.
    pr = make_pr()
    bot_comment = make_comment(body="This PR is a possible duplicate of existing rendering logic.", author={"login": "greptile-apps[bot]"}, authorAssociation="CONTRIBUTOR")

    only_bot = classify_closed_pr(pr, make_evidence(comments=[bot_comment]))
    assert only_bot.classification == "withdrawn"

    with_ship = classify_closed_pr(pr, make_evidence(comments=[bot_comment, make_comment(body="Shipped in v1.0.0, thanks @rodboev")]))
    assert with_ship.classification == "shipped"


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
        # Constituent-list references without a shipping verb are still positive:
        # a release does not have to link back to the PR to prove the work shipped.
        ("Constituent PRs: #9, #10, #11", True),
        ("#10 superseded by #12", False),
        ("Ships #12, dedup-winner over #10", False),
        ("Closes the #10 duplicate", False),
        ("Builds on the groundwork from #10 / #12", False),
        ("Ships #22, does NOT revive the held #10 parallel-bubble mode", False),
        ("Not included - #10 (PowerShell path spaces) - diff didn't match description, sent back to author", False),
        ("Not included - #10 (worktree unification) - data orphaning risk, needs migration plan", False),
        ("#10 fully subsumed by #1491, needs rebase for remaining value", False),
        ("#10 exclusion confirmed correct; the work was intentionally excluded due to data risk", False),
        # "exclusion" alone must read negative: window contexts can truncate
        # before any "excluded" that would otherwise carry the match.
        ("2. **#10 exclusion** - confirmed correct. The worktree-to-paren", False),
        ("#10 (custom embedding) - needs invalid-model validation", False),
        ("no reference to that pull request at all", False),
    ],
)
def test_positive_reference_context_boundary(text: str, expected: bool) -> None:
    assert has_positive_pull_request_reference_context(text, "owner/repo", 10, "rodboev") is expected


def test_release_tag_survives_negative_release_reference_gate(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # The release cross-ref text is negative for #10 (superseded), so it cannot
    # prove shipping — but the release tag itself is still reported.
    pr = make_pr()
    release_ref = make_ref(number=30, title="release: v1.2.3 batch", url="https://github.com/owner/repo/pull/30")
    evidence = make_evidence(
        comments=[make_comment(body="Closing in favor of the batch implementation")],
        timeline_items=[
            make_event(__typename="CrossReferencedEvent", source=release_ref),
            make_event(__typename="ClosedEvent"),
        ],
        reference_text_by_pr={30: "#10 superseded by #12"},
        pull_states_by_pr={30: release_ref},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "superseded"
    assert result.release == "v1.2.3"


def test_direct_merge_takes_release_tag_from_ungated_release_cross_ref(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # Merged directly; the changelog release PR lists it without a shipping verb
    # and the tag still comes through.
    pr = make_pr(state="MERGED", mergedAt="2026-06-01T00:00:00Z")
    release_ref = make_ref(number=30, title="chore: release v9.9.9", url="https://github.com/owner/repo/pull/30")
    evidence = make_evidence(
        timeline_items=[make_event(__typename="CrossReferencedEvent", source=release_ref)],
        reference_text_by_pr={30: ""},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "shipped"
    assert result.evidence_kind == "direct-merge"
    assert result.release == "v9.9.9"


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


@pytest.mark.parametrize(
    ("body", "expected_label"),
    [
        ("Merged via #3601 — cherry-picked onto main.", "#3601"),
        ("Cherry-picked into PR #896, rebased onto current main.", "#896"),
        ("Salvaged into #40561 with authorship credited.", "#40561"),
        ("Shipped in commit `b750c720` on main.", "b750c72"),
        ("cherry-picked in commit ee40084, which addresses the issue", "ee40084"),
        ("Shipped in: `223a2ca` (main)", "223a2ca"),
        ("Merged via rebase onto main. Build clean.", "rebase"),
        ("Merged into the community-edge branch. Thanks!", "rebase"),
    ],
)
def test_ship_comment_via_extraction(body: str, expected_label: str) -> None:
    label, url = get_ship_comment_via(body, "owner/repo", 10)
    assert label == expected_label
    assert url  # always non-empty


def test_ship_comment_via_skips_own_pr_number() -> None:
    label, _ = get_ship_comment_via("Merged via #10 onto main.", "owner/repo", 10)
    assert label == "rebase"


def test_closing_because_with_team_replacement_is_accepted_indirect(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    guard_remover = make_ref(number=7750, author={"login": "teammember"})
    adopting = make_ref(number=7847, author={"login": "maintainer"})
    evidence = make_evidence(
        comments=[
            make_comment(
                body=(
                    "Thanks for the design. Closing because the landscape changed: "
                    "#7750 removed the guard entirely (shipped in v1.4.128), "
                    "and #7847 fixed the residual failure using the same approach you proposed here."
                ),
            ),
        ],
        pull_states_by_pr={7750: guard_remover, 7847: adopting},
        maintainer_logins={"maintainer", "teammember"},
    )

    result = classify_closed_pr(pr, evidence)

    # The credit is the adopting PR named in the "you proposed here" sentence, not the
    # guard-removing PR that merely made the fallback moot.
    assert result.classification == "accepted-indirect"
    assert result.via_label == "#7847"


def test_closing_because_with_third_party_replacement_is_lost(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    replacement = make_ref(number=200, author={"login": "competitor"})
    evidence = make_evidence(
        comments=[
            make_comment(body="Closing because #200 solved this differently"),
        ],
        pull_states_by_pr={200: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "lost"
    assert "competing" in result.log_label


def test_author_close_in_favor_of_credited_takeover_is_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    replacement = make_ref(
        number=9103,
        url="https://github.com/owner/repo/pull/9103",
        author={"login": "nwparker"},
        body=(
            "**Credits:** builds on @rodboev's approach in #10 (same core strip + provider path); "
            "this PR lands it with broader adversarial coverage."
        ),
    )
    evidence = make_evidence(
        comments=[make_comment(body="Closing in favor of #9103.", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")],
        timeline_items=[make_event(actor={"login": "rodboev"})],
        pull_states_by_pr={9103: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"
    assert result.via_label == ""


def test_markdown_author_link_in_takeover_body_counts_as_credit(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # orca#9109 names the author as a markdown profile link and supersedes a third
    # PR in the same sentence; the credit context still reads as credit, and the
    # superseded #8422 gains nothing because its context names no author.
    pr = make_pr()
    replacement = make_ref(
        number=9109,
        url="https://github.com/owner/repo/pull/9109",
        author={"login": "nwparker"},
        body=(
            "Credit: builds on [@rodboev](https://github.com/rodboev)'s approach in #10 "
            "(explicit Off vs auto discriminator). Supersedes the narrower path in #8422 "
            "by covering the shared root cause."
        ),
    )
    evidence = make_evidence(
        comments=[make_comment(body="Closing in favor of #9109.", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")],
        timeline_items=[make_event(actor={"login": "maintainer"})],
        pull_states_by_pr={9109: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "accepted-indirect"
    assert result.via_label == "#9109"


def test_unmerged_takeover_with_credit_stays_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    pr = make_pr()
    replacement = make_ref(
        number=9103,
        state="OPEN",
        merged=False,
        mergedAt="",
        author={"login": "nwparker"},
        body="**Credits:** builds on @rodboev's approach in #10.",
    )
    evidence = make_evidence(
        comments=[make_comment(body="Closing in favor of #9103.", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")],
        timeline_items=[make_event(actor={"login": "rodboev"})],
        pull_states_by_pr={9103: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"


def test_close_in_favor_of_uncredited_competing_pr_stays_withdrawn(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # unsloth#6765: the author yielded to a newer competing PR whose body describes
    # the same fix without naming him or his PR; merging it earns no indirect credit.
    pr = make_pr()
    replacement = make_ref(
        number=6784,
        author={"login": "hakanbaysal"},
        body=(
            "Capture the real parent pid before the fork (in the parent) and compare "
            "the child's getppid() against that instead of the literal 1."
        ),
    )
    evidence = make_evidence(
        comments=[
            make_comment(
                body=(
                    "Closing this in favor of #6784. It covers the same process_lifetime and test "
                    "surfaces, it is newer, and keeping both open would spend review cycles on overlapping fixes."
                ),
                author={"login": "rodboev"},
                authorAssociation="CONTRIBUTOR",
            ),
        ],
        timeline_items=[make_event(actor={"login": "rodboev"})],
        pull_states_by_pr={6784: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
    assert result.evidence_kind == "author-withdrawn"


def test_commit_credit_for_another_pr_is_not_takeover_credit(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # hermes-webui#5845: a release PR ships a different PR by the same author (so he
    # is a commit author there) and mentions this PR only in passing; that commit
    # credit belongs to the other PR and must not upgrade this one.
    pr = make_pr()
    replacement = make_ref(
        number=5845,
        author={"login": "nesquena-hermes"},
        body=(
            "Ships **#5823** (@rodboev) — queue gateway-owned steer fallback so the composer "
            "keeps its draft when steering a live gateway run.\n\n"
            "Gateway sibling of the already-shipped #10 local-agent fallback."
        ),
    )
    evidence = make_evidence(
        comments=[
            make_comment(
                body="Closing this one rather than rebasing it through the current steer stack.",
                author={"login": "rodboev"},
                authorAssociation="CONTRIBUTOR",
            ),
        ],
        timeline_items=[
            make_event(actor={"login": "rodboev"}),
            make_event(source=replacement, **{"__typename": "CrossReferencedEvent"}),
        ],
        pull_states_by_pr={5845: replacement},
        commit_author_logins_by_pr={5845: {"rodboev"}},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"


def test_authors_own_merged_replacement_is_not_a_credited_takeover(
    make_pr: Callable[..., PullRequest],
    make_ref: Callable[..., PullRequestRef],
    make_comment: Callable[..., Comment],
    make_event: Callable[..., TimelineEvent],
    make_evidence: Callable[..., Evidence],
) -> None:
    # The author's own merged resubmit ships directly under its own number; crediting
    # it here too would double-count the same work.
    pr = make_pr()
    replacement = make_ref(
        number=210,
        author={"login": "rodboev"},
        body="Credit: builds on @rodboev's approach in #10.",
    )
    evidence = make_evidence(
        comments=[make_comment(body="Closing in favor of #210.", author={"login": "rodboev"}, authorAssociation="CONTRIBUTOR")],
        timeline_items=[make_event(actor={"login": "rodboev"})],
        pull_states_by_pr={210: replacement},
    )

    result = classify_closed_pr(pr, evidence)

    assert result.classification == "withdrawn"
