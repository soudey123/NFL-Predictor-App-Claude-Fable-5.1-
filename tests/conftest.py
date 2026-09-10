import os
import pytest

# Tests never touch the network or the real database.
os.environ["NFL_OFFLINE"] = "1"


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "OFFLINE", True)
    yield
