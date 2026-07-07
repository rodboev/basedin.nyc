from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JsonObject: TypeAlias = dict[str, object]


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassificationEntry(StrictBaseModel):
    classification: str = ""
    release: str = ""
    viaLabel: str = ""
    viaUrl: str = ""
    evidenceKind: str = ""
    cachedAt: str = ""


class Cache(StrictBaseModel):
    version: int = 3
    entries: dict[str, ClassificationEntry] = Field(default_factory=dict)
    authorPulls: dict[str, JsonObject] = Field(default_factory=dict)
    authorPullScanMeta: dict[str, JsonObject] = Field(default_factory=dict)
    leaderboards: dict[str, JsonObject] = Field(default_factory=dict)
    contributorsMdSeeds: dict[str, JsonObject] = Field(default_factory=dict)
    prAuthorsByNumber: dict[str, str] = Field(default_factory=dict)
    prPullStates: dict[str, JsonObject] = Field(default_factory=dict)
    commitCreditMap: dict[str, JsonObject] = Field(default_factory=dict)
    absorbCommitMap: dict[str, JsonObject] = Field(default_factory=dict)
    mergedPrCreditMap: dict[str, JsonObject] = Field(default_factory=dict)
    absorbedCreditMap: dict[str, JsonObject] = Field(default_factory=dict)
    shipCommentClassifications: dict[str, JsonObject] = Field(default_factory=dict)
    commitScanMeta: dict[str, JsonObject] = Field(default_factory=dict)
    invalid_sections: frozenset[str] = Field(default_factory=frozenset, exclude=True)

    def section_needs_rebuild(self, name: str) -> bool:
        return name in self.invalid_sections


class UserRef(StrictBaseModel):
    login: str = ""


class PullRequestRef(StrictBaseModel):
    number: int = 0
    title: str = ""
    url: str = ""
    merged: bool = False
    mergedAt: str = ""
    state: str = ""
    author: UserRef = Field(default_factory=UserRef)
    body: str = ""


class CommitRef(StrictBaseModel):
    oid: str = ""
    url: str = ""
    messageHeadline: str = ""


class Comment(StrictBaseModel):
    body: str = ""
    author: UserRef = Field(default_factory=UserRef)
    authorAssociation: str = ""


class TimelineEvent(StrictBaseModel):
    typename: str = Field(default="", alias="__typename")
    createdAt: str = ""
    closer: PullRequestRef | None = None
    source: PullRequestRef | None = None
    commit: CommitRef | None = None


class Evidence(StrictBaseModel):
    comments: list[Comment] = Field(default_factory=list)
    timeline_items: list[TimelineEvent] = Field(default_factory=list)
    reference_text_by_pr: dict[int, str] = Field(default_factory=dict)
    pull_states_by_pr: dict[int, PullRequestRef] = Field(default_factory=dict)
    commit_author_logins_by_pr: dict[int, set[str]] = Field(default_factory=dict)
    maintainer_logins: set[str] = Field(default_factory=set)
    integration_bots: set[str] = Field(default_factory=set)
    default_author_login: str = ""


class PullRequest(PullRequestRef):
    repo: str = ""
    repoShort: str = ""
    closedAt: str = ""


def int_value(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default
