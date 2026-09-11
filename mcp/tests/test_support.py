from omarchy_hardware import support
from omarchy_hardware.errors import UNSUPPORTED_OPERATION
from omarchy_hardware.server import list_capabilities


def test_export_includes_every_family():
    families = support.export()
    assert set(families) == set(support.FAMILIES)
    assert families["raspberry_pi"][0]["id"] == "pi.inventory"


def test_unknown_family_is_unsupported():
    result = list_capabilities(family="toaster")
    assert result["ok"] is False
    assert result["error"]["code"] == UNSUPPORTED_OPERATION


def test_list_capabilities_filters_by_family():
    result = list_capabilities(family="jetson")
    assert result["ok"] is True
    assert list(result["families"]) == ["jetson"]
    assert all(row["availability"] == support.AVAIL_UNSUPPORTED for row in result["families"]["jetson"])


def test_pwm_is_unsupported_on_raspberry_pi():
    error = support.unsupported("raspberry_pi", "gpio.pwm")
    assert error.code == UNSUPPORTED_OPERATION
    assert "gpio.pwm" in error.message
    assert "support-matrix.md" in error.hint


def test_capability_names_omit_unsupported_rows():
    names = support.capability_names("raspberry_pi")
    assert "gpio.write" in names
    assert "gpio.pwm" not in names
    assert "gpio.spi" not in names


def test_schneider_and_weintek_are_unsupported():
    for family in ("schneider", "weintek_hmi"):
        result = list_capabilities(family=family)
        assert result["ok"] is True
        rows = result["families"][family]
        assert rows
        assert all(row["availability"] == support.AVAIL_UNSUPPORTED for row in rows)
    weintek_ids = {row["id"] for row in support.export("weintek_hmi")["weintek_hmi"]}
    assert {"hmi.identify", "opcua.read", "opcua.write", "mqtt.subscribe", "mqtt.publish"} <= weintek_ids
    schneider_ids = {row["id"] for row in support.export("schneider")["schneider"]}
    assert {"plc.discover", "plc.read", "plc.write"} <= schneider_ids
