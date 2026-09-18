"""weintek_opcua_read / weintek_opcua_write against a real asyncua server in-process."""

import asyncio
import json
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

asyncua = pytest.importorskip("asyncua")
from asyncua import Server, ua  # noqa: E402
from asyncua.crypto.cert_gen import setup_self_signed_certificate  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID  # noqa: E402

from omarchy_hardware import audit, server  # noqa: E402
from omarchy_hardware.config import Config, OpcUaSecurity, WeintekOpcUaTarget  # noqa: E402
from omarchy_hardware.errors import (  # noqa: E402
    HOST_NOT_ALLOWED,
    INVALID_ARGUMENT,
    SERVICE_ERROR,
    SERVICE_UNREACHABLE,
)
from omarchy_hardware.server import weintek_hmi_identify, weintek_opcua_read, weintek_opcua_write  # noqa: E402

NODES = {
    "ns=2;s=LB-0": (False, ua.VariantType.Boolean),
    "ns=2;s=LW-100": (215, ua.VariantType.Int16),
    "ns=2;s=Setpoint": (21.5, ua.VariantType.Double),
    "ns=2;s=Recipe": ("bread", ua.VariantType.String),
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HmiServer:
    """An OPC UA server shaped like a Weintek HMI's, run on its own event loop."""

    def __init__(self, *, secure_dir: Path | None = None) -> None:
        self.port = _free_port()
        self.endpoint = f"opc.tcp://127.0.0.1:{self.port}"
        self.secure_dir = secure_dir
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._loop.run_until_complete, args=(self._main(),), daemon=True)
        self._thread.start()
        assert self._ready.wait(15), "OPC UA test server did not start"

    async def _main(self) -> None:
        srv = Server()
        await srv.init()
        await srv.set_build_info(
            "urn:weintek:cmt-x:opcua", "Weintek Labs., Inc.", "cMT-X OPC UA Server", "6.10.01", "465",
            datetime(2026, 1, 15, tzinfo=UTC),
        )
        srv.set_endpoint(self.endpoint)
        if self.secure_dir:
            await srv.load_certificate(str(self.secure_dir / "server.der"))
            await srv.load_private_key(str(self.secure_dir / "server.pem"))
            srv.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        else:
            srv.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        idx = await srv.register_namespace("urn:weintek:test")
        assert idx == 2
        folder = await srv.nodes.objects.add_folder(idx, "HMI")
        for node_id, (value, variant) in NODES.items():
            name = node_id.split("=")[-1]
            var = await folder.add_variable(ua.NodeId.from_string(node_id), name, ua.Variant(value, variant))
            await var.set_writable()
        self._stop = asyncio.Event()
        async with srv:
            self._ready.set()
            await self._stop.wait()

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(10)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    monkeypatch.setattr(server, "_actuation", None)


def _use(monkeypatch, endpoint: str, security: OpcUaSecurity, nodes=tuple(NODES)) -> None:
    target = WeintekOpcUaTarget(endpoint, tuple(nodes), security)
    monkeypatch.setattr(server, "_config", lambda: Config(weintek_allow=True, weintek_opcua=(target,)))


INSECURE = OpcUaSecurity(policy="None", mode="None", allow_insecure=True)


@pytest.fixture(scope="module")
def plain_hmi():
    hmi = HmiServer()
    yield hmi
    hmi.close()


def test_identify_reads_the_standard_server_object(monkeypatch, isolated, plain_hmi):
    # No application node is needed: identity comes from the Server object alone.
    _use(monkeypatch, plain_hmi.endpoint, INSECURE, nodes=("ns=2;s=LW-100",))

    who = weintek_hmi_identify(plain_hmi.endpoint)

    assert who["ok"] is True, who
    assert (who["manufacturer"], who["product_name"], who["software_version"], who["build_number"]) == (
        "Weintek Labs., Inc.", "cMT-X OPC UA Server", "6.10.01", "465",
    )
    assert who["reports_weintek"] is True
    assert who["state"] == "Running"
    assert who["build_date"].startswith("2026-01-15")
    assert "urn:weintek:test" in who["namespaces"]


def test_identify_needs_a_listed_endpoint(monkeypatch, isolated, plain_hmi):
    _use(monkeypatch, "opc.tcp://127.0.0.1:1", INSECURE)
    assert weintek_hmi_identify(plain_hmi.endpoint)["error"]["code"] == HOST_NOT_ALLOWED


def test_reads_typed_values(monkeypatch, isolated, plain_hmi):
    _use(monkeypatch, plain_hmi.endpoint, INSECURE)

    read = weintek_opcua_read(plain_hmi.endpoint, "ns=2;s=LW-100")

    assert read["ok"] is True, read
    assert (read["value"], read["type"], read["status"]) == (215, "Int16", "Good")


def test_writes_are_coerced_to_the_node_type_and_audited(monkeypatch, isolated, plain_hmi):
    _use(monkeypatch, plain_hmi.endpoint, INSECURE)

    wrote = weintek_opcua_write(plain_hmi.endpoint, "ns=2;s=Setpoint", "22.25", confirm=True)
    assert wrote["ok"] is True, wrote
    assert (wrote["previous"], wrote["value"], wrote["type"]) == (21.5, 22.25, "Double")

    flag = weintek_opcua_write(plain_hmi.endpoint, "ns=2;s=LB-0", "true", confirm=True)
    assert flag["value"] is True

    events = [json.loads(line)["event"] for line in audit.log_path().read_text().splitlines()]
    assert events == ["weintek_opcua_write", "weintek_opcua_write_done"] * 2
    assert audit.verify()["ok"] is True


@pytest.mark.parametrize(
    ("node", "value"),
    [("ns=2;s=LW-100", "40000"), ("ns=2;s=LW-100", "1.5"), ("ns=2;s=LB-0", "yes"), ("ns=2;s=Setpoint", "nan")],
)
def test_values_that_do_not_fit_the_node_are_refused(monkeypatch, isolated, plain_hmi, node, value):
    _use(monkeypatch, plain_hmi.endpoint, INSECURE)
    before = weintek_opcua_read(plain_hmi.endpoint, node)["value"]

    refused = weintek_opcua_write(plain_hmi.endpoint, node, value, confirm=True)

    assert refused["error"]["code"] == INVALID_ARGUMENT
    assert weintek_opcua_read(plain_hmi.endpoint, node)["value"] == before


def test_unknown_nodes_and_unlisted_nodes(monkeypatch, isolated, plain_hmi):
    _use(monkeypatch, plain_hmi.endpoint, INSECURE, nodes=("ns=2;s=LW-100", "ns=2;s=Missing"))
    assert weintek_opcua_read(plain_hmi.endpoint, "ns=2;s=Recipe")["error"]["code"] == HOST_NOT_ALLOWED
    missing = weintek_opcua_read(plain_hmi.endpoint, "ns=2;s=Missing")
    assert missing["error"]["code"] == SERVICE_ERROR
    assert "BadNodeIdUnknown" in missing["error"]["message"]


def test_an_unreachable_endpoint_is_reported(monkeypatch, isolated):
    endpoint = f"opc.tcp://127.0.0.1:{_free_port()}"
    _use(monkeypatch, endpoint, INSECURE)
    assert weintek_opcua_read(endpoint, "ns=2;s=LW-100")["error"]["code"] == SERVICE_UNREACHABLE


def _certificates(directory: Path, name: str, usage) -> None:
    asyncio.run(setup_self_signed_certificate(
        directory / f"{name}.pem", directory / f"{name}.der", f"urn:omarchy:{name}", "127.0.0.1", [usage],
        {"commonName": name},
    ))


def test_signed_and_encrypted_session_with_pinned_certificate(monkeypatch, isolated, tmp_path):
    _certificates(tmp_path, "server", ExtendedKeyUsageOID.SERVER_AUTH)
    _certificates(tmp_path, "client", ExtendedKeyUsageOID.CLIENT_AUTH)
    _certificates(tmp_path, "impostor", ExtendedKeyUsageOID.SERVER_AUTH)
    hmi = HmiServer(secure_dir=tmp_path)
    try:
        secure = OpcUaSecurity(
            certificate=str(tmp_path / "client.der"),
            private_key=str(tmp_path / "client.pem"),
            trust_list=str(tmp_path / "server.der"),
        )
        _use(monkeypatch, hmi.endpoint, secure)
        read = weintek_opcua_read(hmi.endpoint, "ns=2;s=Recipe")
        assert read["ok"] is True, read
        assert read["value"] == "bread"

        # Pinned to a different certificate, the channel must not come up.
        wrong_pin = OpcUaSecurity(
            certificate=str(tmp_path / "client.der"),
            private_key=str(tmp_path / "client.pem"),
            trust_list=str(tmp_path / "impostor.der"),
        )
        _use(monkeypatch, hmi.endpoint, wrong_pin)
        assert weintek_opcua_read(hmi.endpoint, "ns=2;s=Recipe")["ok"] is False
    finally:
        hmi.close()
