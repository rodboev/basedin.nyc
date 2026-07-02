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
