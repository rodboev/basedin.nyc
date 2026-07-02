from __future__ import annotations

from core.credit import (
    CreditVerificationContext,
    PullRequestCreditState,
    best_ship_comment_classification,
    confirm_upstream_release_credit_map,
    credit_count_map,
    get_webui_changelog_credit_map,
    github_login_from_coauthor_trailer,
    invoke_ship_comment_classifier,
    merge_credit_maps,
)


def test_changelog_credit_map_parses_supported_formats() -> None:
    text = """
- **PR #101** by @alice
**PR #102** by @bob
PR #103 by @carol
@dana - PR #104
(credit: @erin) carried PR #105
PR #106 included (credit: @frank)
(#107 / 108, @grace)
"""

    result = get_webui_changelog_credit_map(text)

    assert result["alice"] == {101}
    assert result["bob"] == {102}
    assert result["carol"] == {103}
    assert result["dana"] == {104}
    assert result["erin"] == {105}
    assert result["frank"] == {106}
    assert result["grace"] == {107}


def test_merge_credit_maps_dedupes_pairs() -> None:
    merged = merge_credit_maps(({"alice": [1, 2]}, {"alice": [2, 3], "bob": [4]}))

    assert merged == {"alice": {1, 2, 3}, "bob": {4}}
    assert credit_count_map(merged) == {"alice": 3, "bob": 1}


def test_merge_credit_maps_collapses_login_case_like_powershell_hashtables() -> None:
    merged = merge_credit_maps(({"FrankSong2702": [1]}, {"franksong2702": [2]}))

    assert merged == {"FrankSong2702": {1, 2}}


def test_github_login_from_coauthor_trailer() -> None:
    assert github_login_from_coauthor_trailer("Co-authored-by: Rod <106971+rodboev@users.noreply.github.com>") == "rodboev"
    assert github_login_from_coauthor_trailer("Co-authored-by: Bot <bot@users.noreply.github.com>") == "bot"
    assert github_login_from_coauthor_trailer("Signed-off-by: no github email") is None


def test_ship_comment_classifier_plain_and_own_ship() -> None:
    assert (
        invoke_ship_comment_classifier(
            pr_number=10,
            comment_body="This shipped in v1.2.3",
            pr_author_login="alice",
            pull_request_author=lambda number: None,
        )
        == "plain-ship"
    )
    assert (
        invoke_ship_comment_classifier(
            pr_number=10,
            comment_body="Your commits were carried forward into #20",
            pr_author_login="alice",
            pull_request_author=lambda number: None,
        )
        == "own-ship"
    )


def test_ship_comment_deflection_resolves_own_superseding_pr() -> None:
    result = invoke_ship_comment_classifier(
        pr_number=10,
        comment_body="Closing as superseded by #20",
        pr_author_login="alice",
        pull_request_author=lambda number: "alice" if number == 20 else None,
    )

    assert result == "own-ship"


def test_ship_comment_deflection_resolves_coauthor_index() -> None:
    result = invoke_ship_comment_classifier(
        pr_number=10,
        comment_body="Closing as superseded by #20",
        pr_author_login="alice",
        pull_request_author=lambda number: "maintainer",
        coauthor_index={20: {"alice"}},
    )

    assert result == "co-author-ship"


def test_ship_comment_deflection_falls_back_to_commit_messages() -> None:
    result = invoke_ship_comment_classifier(
        pr_number=10,
        comment_body="Closing as superseded by #20",
        pr_author_login="alice",
        pull_request_author=lambda number: "maintainer",
        commit_messages_for_pr=lambda number: ["implement\n\nCo-authored-by: alice <1+alice@users.noreply.github.com>"],
    )

    assert result == "co-author-ship"


def test_ship_comment_deflection_without_credit_is_deflection() -> None:
    result = invoke_ship_comment_classifier(
        pr_number=10,
        comment_body="Closing as superseded by #20",
        pr_author_login="alice",
        pull_request_author=lambda number: "maintainer",
    )

    assert result == "deflection"


def test_best_ship_comment_classification_priority() -> None:
    assert best_ship_comment_classification(["plain-ship", "deflection", "co-author-ship"]) == "co-author-ship"
    assert best_ship_comment_classification([None, "deflection"]) == "deflection"
    assert best_ship_comment_classification([None]) == "none"


def test_confirm_release_credit_map_preserves_source_specific_rules() -> None:
    context = CreditVerificationContext(
        pull_requests={
            1: PullRequestCreditState(author_login="alice", state="MERGED"),
            2: PullRequestCreditState(author_login="alice", state="CLOSED", classification="accepted-indirect"),
            3: PullRequestCreditState(author_login="alice", state="CLOSED"),
            4: PullRequestCreditState(author_login="alice", state="CLOSED"),
            5: PullRequestCreditState(author_login="alice", state="CLOSED"),
            6: PullRequestCreditState(author_login="alice", state="CLOSED"),
            7: PullRequestCreditState(author_login="bob", state="MERGED"),
            8: PullRequestCreditState(author_login="alice", state="MERGED", title="Release v1.2.3"),
        },
        excluded_logins=frozenset({"alice"}),
    )

    verified = confirm_upstream_release_credit_map(
        changelog_map={"alice": {1, 8}},
        commit_map={"alice": {2}},
        merged_map={"alice": {3}},
        absorbed_map={"alice": {4}},
        absorb_commit_map={"alice": {5}},
        ship_comment_map={"alice": {6}},
        context=context,
    )

    assert verified == {"alice": {1, 2, 3, 4, 5, 6}}


def test_confirm_release_credit_map_ownership_is_case_insensitive() -> None:
    context = CreditVerificationContext(
        pull_requests={
            1: PullRequestCreditState(author_login="franksong2702", state="MERGED"),
        },
    )

    verified = confirm_upstream_release_credit_map(
        changelog_map={"FrankSong2702": {1}},
        commit_map={},
        merged_map={},
        absorbed_map={},
        context=context,
    )

    assert verified == {"FrankSong2702": {1}}


def test_confirm_release_credit_map_rejects_wrong_author_and_unmerged_changelog() -> None:
    context = CreditVerificationContext(
        pull_requests={
            1: PullRequestCreditState(author_login="bob", state="MERGED"),
            2: PullRequestCreditState(author_login="alice", state="CLOSED"),
        },
    )

    verified = confirm_upstream_release_credit_map(
        changelog_map={"alice": {1, 2}},
        commit_map={},
        merged_map={},
        absorbed_map={},
        context=context,
    )

    assert verified == {}


def test_release_vehicle_exclusion_uses_bot_and_case_insensitive_exclusions() -> None:
    context = CreditVerificationContext(
        pull_requests={
            1: PullRequestCreditState(author_login="Dependabot[bot]", state="MERGED", title="Release v1.2.3"),
            2: PullRequestCreditState(author_login="Maintainer", state="MERGED", title="Release v1.2.4"),
        },
        excluded_logins=frozenset({"maintainer"}),
    )

    verified = confirm_upstream_release_credit_map(
        changelog_map={"Dependabot[bot]": {1}, "Maintainer": {2}},
        commit_map={},
        merged_map={},
        absorbed_map={},
        context=context,
    )

    assert verified == {}
