import pytest

from omarchy_hardware import journal


@pytest.fixture(autouse=True)
def _private_journal(tmp_path_factory, monkeypatch):
    """Keep every test's board journal out of the real ~/.local/state."""
    monkeypatch.setattr(journal, "STATE_DIR", tmp_path_factory.mktemp("state"))
