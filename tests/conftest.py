from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _repo_root(monkeypatch):
    """Config paths are repo-relative; run every test from the repo root."""
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
