import json

from omarchy_hardware import boards
from omarchy_hardware.ids import BOARDS


def test_scan_catalog_includes_uno_and_only_identifiable_targets(monkeypatch, capsys):
    monkeypatch.setattr(boards, "enumerate_boards", lambda: [])
    boards.main()
    result = json.loads(capsys.readouterr().out)
    catalog = result["supported_boards"]
    assert "Arduino Uno" in catalog
    assert "Arduino UNO R4 Minima" in catalog
    assert "BBC micro:bit v2" not in catalog
    assert not any("ST-LINK" in name for name in catalog)
    assert catalog == sorted({info.friendly_name for info in BOARDS.values()
                              if info.board_type != "unknown" and info.fqbn})
    assert result["ok"] is True
    assert result["boards"] == []
