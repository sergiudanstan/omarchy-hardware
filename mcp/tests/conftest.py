import pytest

from omarchy_hardware import audit, journal, slack_bridge

# The user's real state directory, captured before any test can repoint it.
REAL_STATE_DIR = audit.STATE_DIR


@pytest.fixture(autouse=True)
def _private_state(tmp_path_factory, monkeypatch):
    """Keep every test's journal, audit log and Slack workspace out of the real ~/.local/state.

    The audit log is the user's tamper-evident record of what touched their hardware;
    a test that reaches it (as one did, adding a fake restore failure on every run)
    corrupts exactly the evidence it exists to keep.
    """
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(journal, "STATE_DIR", state)
    monkeypatch.setattr(audit, "STATE_DIR", state)
    monkeypatch.setattr(slack_bridge, "STATE_DIR", state)
    audit._chain.reset()


@pytest.fixture(autouse=True)
def _no_live_rp2(request, monkeypatch):
    """RP2 sysfs walks must not see the workstation's Pico during other tests."""
    if request.module.__name__.endswith("test_rp2"):
        return
    monkeypatch.setattr("omarchy_hardware.boards.enumerate_rp2_devices", lambda: [])
    monkeypatch.setattr("omarchy_hardware.server.enumerate_rp2_devices", lambda: [])
