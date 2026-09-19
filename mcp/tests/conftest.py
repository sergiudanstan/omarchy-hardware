import pytest

from omarchy_hardware import journal


@pytest.fixture(autouse=True)
def _private_journal(tmp_path_factory, monkeypatch):
    """Keep every test's board journal out of the real ~/.local/state."""
    monkeypatch.setattr(journal, "STATE_DIR", tmp_path_factory.mktemp("state"))


@pytest.fixture(autouse=True)
def _no_live_rp2(request, monkeypatch):
    """RP2 sysfs walks must not see the workstation's Pico during other tests."""
    if request.module.__name__.endswith("test_rp2"):
        return
    monkeypatch.setattr("omarchy_hardware.boards.enumerate_rp2_devices", lambda: [])
    monkeypatch.setattr("omarchy_hardware.server.enumerate_rp2_devices", lambda: [])
