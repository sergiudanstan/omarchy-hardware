import pytest

from omarchy_hardware import displays, errors, peripherals, wiring


@pytest.fixture(autouse=True)
def clear_display_cache():
    displays.table.cache_clear()
    yield
    displays.table.cache_clear()


def test_catalog_is_sorted_and_lookup_is_case_insensitive():
    entries = displays.catalog()
    names = [entry["controller"] for entry in entries]
    assert names == sorted(names, key=str.casefold)
    assert displays.lookup("iLi9341") == next(entry for entry in entries if entry["controller"] == "ILI9341")


def test_catalog_roles_exist_in_wiring_and_i2c_addresses_match_peripherals():
    catalog = displays.catalog()
    for display in catalog:
        assert all(role in wiring.ROLES for roles in display["roles"].values() for role in roles)

    expected = {
        address
        for device in peripherals.table()
        if device["kind"] == "display"
        for address in device["addresses"]
    }
    catalog_addresses = {
        address for display in catalog for address in display.get("i2c_addresses", [])
    }
    assert catalog_addresses == expected


def test_export_labels_catalog_data_as_unvalidated():
    result = displays.export()
    assert result["displays"] == displays.catalog()
    assert "not physically validated" in result["data_status"]
    assert "breakout boards differ" in result["data_status"]


def test_unknown_controller_error_hints_known_controllers():
    with pytest.raises(errors.ToolError) as caught:
        displays.lookup("not-a-display")
    assert caught.value.code == errors.INVALID_ARGUMENT
    assert "Known controllers:" in caught.value.hint
    assert "SSD1306" in caught.value.hint


def test_unknown_catalog_key_fails_at_load(tmp_path, monkeypatch):
    source = displays.DATA.read_text()
    source = source.replace('controller = "SSD1306"', 'controller = "SSD1306"\nunknown = true', 1)
    data = tmp_path / "displays.toml"
    data.write_text(source)
    monkeypatch.setattr(displays, "DATA", data)
    with pytest.raises(ValueError, match="unexpected or missing keys"):
        displays.catalog()
