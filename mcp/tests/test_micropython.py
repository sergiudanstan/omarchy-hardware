"""MicroPython raw REPL over a PTY, against a fake board that speaks the protocol."""

import binascii
import builtins
import contextlib
import io
import json
import os
import pty
import select
import threading
import time

import pytest

from omarchy_hardware import audit, errors, micropython, server
from omarchy_hardware.config import Config
from omarchy_hardware.errors import ToolError
from omarchy_hardware.serial_session import SerialSession


class FakeBoard(threading.Thread):
    """Enough of MicroPython's REPL: Ctrl-A/B/C/D, code execution, a tiny filesystem."""

    def __init__(self, master: int) -> None:
        super().__init__(daemon=True)
        self.master = master
        self.files: dict[str, bytes] = {"/boot.py": b"# boot\n"}
        self.raw = False
        self.code = b""
        self.stuck = False
        self.running = True
        self.received = b""

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def _run_code(self, source: str) -> tuple[str, str]:
        files = self.files

        class Handle(io.BytesIO):
            def __init__(self, path, mode):
                super().__init__()
                self.path = path
                if "a" in mode:
                    self.write(files.get(path, b""))

            def close(self):
                files[self.path] = self.getvalue()
                super().close()

        class FakeOs:
            @staticmethod
            def ilistdir(path):
                return [(name.strip("/"), 0x8000, 0, len(data)) for name, data in files.items()]

            @staticmethod
            def stat(path):
                return (0x8000, 0, 0, 0, 0, 0, len(files[path]), 0, 0, 0)

        modules = {"os": FakeOs, "ubinascii": binascii, "json": json}
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            return modules[name] if name in modules else real_import(name, *args, **kwargs)

        env_builtins = dict(vars(builtins), __import__=fake_import, open=lambda path, mode="r": Handle(path, mode))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                exec(source, {"__builtins__": env_builtins})  # noqa: S102 - test fixture running test-chosen code
            except Exception as exc:  # noqa: BLE001
                err.write(f"Traceback (most recent call last):\r\n{type(exc).__name__}: {exc}\r\n")
        return out.getvalue(), err.getvalue()

    def run(self) -> None:
        while self.running:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            self.received += data
            for byte in data:
                char = bytes([byte])
                if self.stuck:
                    if char == b"\x03":
                        self.stuck = False
                        self.send(b"\x04Traceback (most recent call last):\r\nKeyboardInterrupt: \r\n\x04>")
                    continue
                if not self.raw:
                    if char == b"\x01":
                        self.raw, self.code = True, b""
                        self.send(micropython.RAW_BANNER.encode())
                    elif char == b"\x03":
                        self.send(b"\r\n>>> ")
                    continue
                if char == b"\x02":
                    self.raw = False
                    self.send(b"\r\nMicroPython v1.22.2 on 2024-02-22; Raspberry Pi Pico with RP2040\r\n>>> ")
                elif char == b"\x04":
                    source, self.code = self.code.decode(), b""
                    self.send(b"OK")
                    if "while True" in source:
                        self.stuck = True
                        continue
                    stdout, stderr = self._run_code(source)
                    self.send(stdout.replace("\n", "\r\n").encode() + b"\x04" + stderr.encode() + b"\x04>")
                elif char == b"\x03":
                    self.code = b""
                else:
                    self.code += char


@pytest.fixture
def board():
    master, slave = pty.openpty()
    fake = FakeBoard(master)
    fake.start()
    session = SerialSession(os.ttyname(slave), 115200)
    yield fake, session
    fake.running = False
    session.close()
    os.close(master)
    os.close(slave)


def test_exec_returns_stdout_and_leaves_raw_mode(board):
    fake, session = board
    result = micropython.run(session, "print(6 * 7)\nprint('hi')", 5000)
    assert result == {"finished": True, "left_raw_mode": True, "stdout": "42\r\nhi\r\n", "stderr": "",
                      "untrusted": True}
    assert fake.raw is False
    assert fake.received.startswith(b"\r\x03\x03")


def test_exec_reports_tracebacks(board):
    _fake, session = board
    result = micropython.run(session, "1/0", 5000)
    assert result["finished"] and "ZeroDivisionError" in result["stderr"]


def test_long_code_is_sent_in_chunks(board):
    _fake, session = board
    code = "x = 0\n" + "x += 1\n" * 200 + "print(x)\n"
    assert micropython.run(session, code, 5000)["stdout"].strip() == "200"


def test_code_past_the_deadline_is_interrupted(board):
    fake, session = board
    started = time.monotonic()
    result = micropython.run(session, "while True:\n    pass\n", 400)
    assert time.monotonic() - started < 3
    assert result["finished"] is False
    assert fake.stuck is False, "Ctrl-C was sent"


def test_put_and_list(board):
    fake, session = board
    content = ("print('hello from main')\n" * 400).encode()
    programs = micropython.put_programs("/main.py", content)
    assert len(programs) == 4 and all(len(p) < 5000 for p in programs)
    for program in programs:
        micropython.run(session, program, 5000)
    assert fake.files["/main.py"] == content
    listing = micropython.parse_listing(micropython.run(session, micropython.list_code("/"), 5000)["stdout"])
    assert {"name": "main.py", "type": "file", "size": len(content)} in listing


@pytest.mark.parametrize("path", ["", "../x", "a/../b", "/main.py'); import os; os.remove('boot.py", "a b", "x" * 200])
def test_paths_are_validated(path):
    with pytest.raises(ToolError) as caught:
        micropython.check_path(path)
    assert caught.value.code == errors.INVALID_ARGUMENT


def test_file_content_never_becomes_source():
    [code] = micropython.put_programs("/x.py", b"')\nimport os\nos.remove('/boot.py')\n#")
    assert "os.remove" not in code
    with pytest.raises(ToolError):
        micropython.put_programs("/x.py", b"x" * (micropython.MAX_FILE_BYTES + 1))


def test_a_board_without_micropython_is_reported(monkeypatch):
    master, slave = pty.openpty()
    session = SerialSession(os.ttyname(slave), 115200)
    try:
        monkeypatch.setattr(micropython, "_read_until", lambda s, t, d: (b"garbage", False))
        with pytest.raises(ToolError, match="raw REPL"):
            micropython.run(session, "print(1)", 500)
    finally:
        session.close()
        os.close(master)
        os.close(slave)


@pytest.fixture
def tools(board, monkeypatch, tmp_path):
    fake, session = board
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    monkeypatch.setattr(server, "_budget", None)
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=True))
    monkeypatch.setattr(server.sessions, "get", lambda session_id: session)
    return fake


def test_tools_need_confirm_and_are_audited(tools):
    fake = tools
    assert server.mpy_exec("s", "print(1)")["error"]["code"] == errors.UNCONFIRMED
    assert server.mpy_put("s", "/main.py", "x")["error"]["code"] == errors.UNCONFIRMED

    ran = server.mpy_exec("s", "print(1 + 1)", confirm=True)
    assert ran["ok"] and ran["stdout"].strip() == "2" and ran["untrusted"] is True
    put = server.mpy_put("s", "/main.py", "print('blink')\n", confirm=True)
    assert put["ok"] and put["chunks"] == 1 and fake.files["/main.py"] == b"print('blink')\n"
    listed = server.mpy_list("s")
    assert any(entry["name"] == "main.py" for entry in listed["entries"])

    events = [json.loads(line)["event"] for line in audit.log_path().read_text().splitlines()]
    assert "micropython_exec" in events and "micropython_put" in events and "micropython_list" in events


def test_exec_size_cap(tools, monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=True, max_write_bytes=16))
    assert server.mpy_exec("s", "print('a long line of code')", confirm=True)["error"]["code"] == errors.WRITE_TOO_LARGE
