import os
import stat
import subprocess
from pathlib import Path

import pytest

from omarchy_hardware import audit, crash, errors, flash, journal, server

XTENSA_PANIC = """Guru Meditation Error: Core  1 panic'ed (StoreProhibited). Exception was unhandled.

Core  1 register dump:
PC      : 0x400d162e  PS      : 0x00060830  A0      : 0x800d1670  A1      : 0x3ffb2260
EXCVADDR: 0x00000000  LBEG    : 0x40086a05  LEND    : 0x40086a15  LCOUNT  : 0xffffffff

Backtrace: 0x400d162b:0x3ffb2260 0x400d166d:0x3ffb2280 0x400d166d:0x3ffb2280 0x00000000:0x00000000
"""
RISCV_PANIC = """Guru Meditation Error: Core  0 panic'ed (Load access fault). Exception was unhandled.

Core  0 register dump:
MEPC    : 0x42000a2c  RA      : 0x42000a1a  SP      : 0x3fc9a1e0  GP      : 0x3fc8d000
"""
ABORT = "abort() was called at PC 0x400e1c2f on core 1\n\nBacktrace: 0x40083b39:0x3ffb1e20 0x400e1c2f:0x3ffb1e40\n"
UNO = {"port": "/dev/ttyACM0", "vid": "303a", "pid": "1001", "serial": "S1", "board_type": "esp32",
       "friendly_name": "ESP32-S3", "suggested_fqbn": "esp32:esp32:esp32s3"}
DIGEST = "a" * 64


def test_xtensa_panic():
    report = crash.parse(XTENSA_PANIC)
    assert report["kind"] == "esp_xtensa_backtrace"
    assert report["reason"].startswith("Guru Meditation Error: Core  1 panic'ed (StoreProhibited)")
    # PC first, backtrace PCs after, duplicates and zero frames dropped, SPs never included.
    assert report["addresses"] == ["0x400d162e", "0x400d162b", "0x400d166d"]


def test_riscv_panic_and_abort():
    riscv = crash.parse(RISCV_PANIC)
    assert riscv["kind"] == "esp_riscv_panic" and riscv["addresses"] == ["0x42000a2c", "0x42000a1a"]
    abort = crash.parse(ABORT)
    assert abort["addresses"][0] == "0x400e1c2f"
    assert abort["reason"].startswith("abort() was called")


def test_non_crash_text_and_reason_only():
    assert crash.parse("hello\nworld\n") is None
    only = crash.parse("***ERROR*** A stack overflow in task loopTask has been detected.\n")
    assert only["kind"] == "reset_reason_only" and only["addresses"] == []
    with pytest.raises(errors.ToolError):
        crash.parse(["not", "text"])


def test_long_input_is_bounded():
    report = crash.parse(("x" * 100_000) + XTENSA_PANIC + "Backtrace: " + " ".join(
        f"0x4{n:07x}:0x3ffb0000" for n in range(1, 200)))
    assert len(report["addresses"]) == crash.MAX_ADDRESSES


def _elf(path: Path, machine: int) -> Path:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4], header[5] = 1, 1  # 32-bit, little endian
    header[18:20] = machine.to_bytes(2, "little")
    path.write_bytes(bytes(header))
    return path


def test_elf_machine(tmp_path):
    assert crash.elf_machine(_elf(tmp_path / "a.elf", 94)) == 94
    (tmp_path / "b.elf").write_bytes(b"not an elf at all, really")
    assert crash.elf_machine(tmp_path / "b.elf") is None


FAKE_ADDR2LINE = """#!/bin/sh
# Echo the -a -f -i format for every address after -e ELF.
while [ "$1" != "-e" ]; do shift; done
shift; shift
for address in "$@"; do
  echo "$address"
  case "$address" in
    0x400d162e) echo "explode()"; echo "/src/crashy.ino:4"; echo "loop()"; echo "/src/crashy.ino:12 (discriminator 1)";;
    0x400d166d) echo "loop()"; echo "/src/crashy.ino:12";;
    *) echo "??"; echo "??:0";;
  esac
done
"""


@pytest.fixture
def toolchain(tmp_path, monkeypatch):
    tools = tmp_path / "arduino15" / "packages" / "esp32" / "tools"
    binary = tools / "esp-x32" / "2601" / "bin" / "xtensa-esp-elf-addr2line"
    binary.parent.mkdir(parents=True)
    binary.write_text(FAKE_ADDR2LINE)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("OMARCHY_HARDWARE_ARDUINO_DATA", str(tmp_path / "arduino15"))
    return binary


def test_find_addr2line_by_machine(toolchain):
    assert crash.find_addr2line(94) == str(toolchain)
    assert crash.find_addr2line(243) is None
    assert crash.find_addr2line(3) is None


def test_decode_with_inlined_frames(toolchain, tmp_path):
    frames = crash.decode(_elf(tmp_path / "fw.elf", 94), ["0x400d162e", "0x400d166d", "0x400d9999"])
    assert frames[0] == {"address": "0x400d162e", "function": "explode()", "file": "/src/crashy.ino", "line": 4,
                         "inlined_into": [{"function": "loop()", "file": "/src/crashy.ino", "line": 12}]}
    assert frames[1]["function"] == "loop()" and frames[1]["line"] == 12
    assert frames[2] == {"address": "0x400d9999", "function": None, "file": None, "line": None}


def test_decode_refuses_non_addresses_and_missing_toolchains(toolchain, tmp_path):
    elf = _elf(tmp_path / "fw.elf", 94)
    with pytest.raises(errors.ToolError):
        crash.decode(elf, ["0x400d162e; rm -rf ~"])
    with pytest.raises(errors.ToolError) as caught:
        crash.decode(_elf(tmp_path / "rv.elf", 243), ["0x42000a2c"])
    assert caught.value.code == errors.TOOL_MISSING


def test_store_elf_keeps_the_last_three_privately(tmp_path):
    source = _elf(tmp_path / "fw.elf", 94)
    stored = []
    for n in range(5):
        path = journal.store_elf(UNO, f"{n:064x}", str(source))
        os.utime(path, (n, n))
        stored.append(path)
    kept = sorted(p.name for p in stored[0].parent.glob("*.elf"))
    assert kept == [f"{n:064x}.elf" for n in (2, 3, 4)]
    assert stat.S_IMODE(stored[0].parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored[-1].stat().st_mode) == 0o600
    assert journal.elf_path(UNO, f"{4:064x}") == stored[-1]
    assert journal.store_elf(UNO, "../../evil", str(source)) is None
    assert journal.elf_path(UNO, "../../evil") is None
    assert journal.store_elf({**UNO, "serial": None}, DIGEST, str(source)) is None


def test_upload_hands_the_snapshot_elf_to_the_callback(monkeypatch, tmp_path):
    sketch = tmp_path / "blink"
    sketch.mkdir()
    artifact = tmp_path / "build"
    artifact.mkdir()
    (artifact / "blink.ino.bin").write_bytes(b"firmware")
    _elf(artifact / "blink.ino.elf", 94)
    digest = flash._artifact_digest(str(artifact))
    token = flash.mint_token(str(sketch), "esp32:esp32:esp32", "", str(artifact), digest)
    monkeypatch.setattr(flash, "_prepare_upload_log", lambda record: None)
    monkeypatch.setattr(audit, "note", lambda *a, **k: None)
    kept = []

    def keep(path):
        kept.append(Path(path).read_bytes()[:4])

    for code in (1, 0):
        def cli(args, code=code):
            return subprocess.CompletedProcess(args, code, "", "")

        monkeypatch.setattr(flash, "_arduino_cli", cli)
        flash.upload_sketch(str(sketch), "/dev/ttyACM0", "esp32:esp32:esp32", token, artifact_path=str(artifact),
                            artifact_digest=digest, roots=(str(tmp_path),), keep_elf=keep)
    assert kept == [b"\x7fELF"], "only the successful upload keeps its ELF"


def test_decode_crash_tool_end_to_end(toolchain, tmp_path, monkeypatch):
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: UNO["port"])
    monkeypatch.setattr(server, "enumerate_boards", lambda: [UNO])
    journal.record_upload(UNO, fqbn="esp32:esp32:esp32s3", sketch_dir="/src/crashy", artifact_digest=DIGEST)

    missing = server.decode_crash("/dev/ttyACM0", XTENSA_PANIC)
    assert missing["error"]["code"] == errors.ARTIFACT_INVALID

    journal.store_elf(UNO, DIGEST, str(_elf(tmp_path / "fw.elf", 94)))
    result = server.decode_crash("/dev/ttyACM0", XTENSA_PANIC)
    assert result["ok"] is True and result["sketch"] == "crashy"
    assert result["frames"][0]["function"] == "explode()"
    assert server.decode_crash("/dev/ttyACM0", "all fine")["error"]["code"] == errors.INVALID_ARGUMENT
    unknown = server.decode_crash("/dev/ttyACM0", XTENSA_PANIC, artifact_digest="b" * 64)
    assert unknown["error"]["code"] == errors.JOURNAL_UNAVAILABLE


def test_addr2line_timeout_and_exec_failure_are_reported(toolchain, tmp_path, monkeypatch):
    elf = _elf(tmp_path / "fw.elf", 94)

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired("addr2line", crash.ADDR2LINE_TIMEOUT)

    monkeypatch.setattr(crash.subprocess, "run", hang)
    with pytest.raises(errors.ToolError) as caught:
        crash.decode(elf, ["0x400d162e"])
    assert caught.value.code == errors.SERVICE_ERROR and "longer than" in caught.value.message

    def broken(*args, **kwargs):
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(crash.subprocess, "run", broken)
    with pytest.raises(errors.ToolError) as caught:
        crash.decode(elf, ["0x400d162e"])
    assert caught.value.code == errors.TOOL_MISSING


def test_flashed_elf_prefers_the_sketch_and_refuses_ambiguity(tmp_path):
    build = tmp_path / "build"
    (build / "bootloader").mkdir(parents=True)
    (build / "bootloader" / "bootloader.elf").write_bytes(b"x")
    (build / "partitions.elf").write_bytes(b"x")
    assert flash._flashed_elf(str(build)) is None, "two ELFs and no sketch ELF: refuse to guess"
    (build / "blink.ino.elf").write_bytes(b"x")
    assert flash._flashed_elf(str(build)).endswith("blink.ino.elf")
    nested = tmp_path / "nested"
    (nested / "out").mkdir(parents=True)
    (nested / "out" / "only.elf").write_bytes(b"x")
    assert flash._flashed_elf(str(nested)).endswith("only.elf")


def test_upload_reports_when_the_elf_is_not_kept(monkeypatch, tmp_path):
    sketch = tmp_path / "blink"
    sketch.mkdir()
    artifact = tmp_path / "build"
    artifact.mkdir()
    (artifact / "blink.ino.bin").write_bytes(b"firmware")
    digest = flash._artifact_digest(str(artifact))
    token = flash.mint_token(str(sketch), "esp32:esp32:esp32", "", str(artifact), digest)
    monkeypatch.setattr(flash, "_prepare_upload_log", lambda record: None)
    monkeypatch.setattr(audit, "note", lambda *a, **k: None)
    monkeypatch.setattr(flash, "_arduino_cli", lambda args, **kw: subprocess.CompletedProcess(args, 0, "", ""))
    result = flash.upload_sketch(str(sketch), "/dev/ttyACM0", "esp32:esp32:esp32", token, artifact_path=str(artifact),
                                 artifact_digest=digest, roots=(str(tmp_path),), keep_elf=lambda path: True)
    assert result["ok"] is True and result["elf_kept"] is False and "no single sketch ELF" in result["elf_note"]
