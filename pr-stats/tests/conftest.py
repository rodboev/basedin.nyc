from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from core.models import Cache


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def live_cache_path(repo_root: Path) -> Path:
    return repo_root / ".pr-classification-cache.json"


@pytest.fixture
def make_cache_file(tmp_path: Path) -> Callable[[bytes], Path]:
    def factory(content: bytes) -> Path:
        path = tmp_path / "cache.json"
        path.write_bytes(content)
        return path

    return factory


@pytest.fixture
def empty_loaded_cache() -> Cache:
    return Cache()

