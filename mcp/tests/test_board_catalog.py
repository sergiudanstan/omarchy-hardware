import json

from omarchy_hardware import boards
from omarchy_hardware.ids import BOARDS, nucleo_boards


def test_scan_catalog_includes_uno_and_only_identifiable_targets(monkeypatch, capsys):
    monkeypatch.setattr(boards, "enumerate_boards", lambda: [])
    boards.main()
    result = json.loads(capsys.readouterr().out)
    catalog = result["supported_boards"]
    assert "Arduino Uno" in catalog
    assert "Arduino UNO R4 Minima" in catalog
    assert "BBC micro:bit v2" not in catalog
    assert not any("ST-LINK" in name for name in catalog)
    assert "STM32 Nucleo-F411RE" in catalog
    assert catalog == sorted({info.friendly_name for info in [*BOARDS.values(), *nucleo_boards()]
                              if info.board_type != "unknown" and info.fqbn})
    assert result["ok"] is True
    assert result["boards"] == []


def test_scan_output_carries_journal_labels(monkeypatch, capsys):
    from omarchy_hardware import journal

    board = {"port": "/dev/ttyACM0", "vid": "2341", "pid": "0043", "serial": "A1", "board_type": "arduino_uno"}
    journal.set_label(board, "greenhouse-node")
    monkeypatch.setattr(boards, "enumerate_boards", lambda: [board, {**board, "port": "/dev/ttyUSB0", "serial": None}])
    boards.main()
    result = json.loads(capsys.readouterr().out)
    assert [b["label"] for b in result["boards"]] == ["greenhouse-node", None]
