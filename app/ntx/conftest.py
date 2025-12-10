from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def data_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data"


@pytest.fixture
def media_root(tmp_path, settings) -> Path:
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


@pytest.fixture
def stored_data_dir(data_dir: Path, media_root: Path) -> Path:
    dest = media_root / "axion" / data_dir.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(data_dir, dest)
    return dest
