"""Run the demo with recording clients; never use real credentials or hardware."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples/ming-stack/demo/greenhouse.sh"
PASSWORD = "greenhouse-regression-fixture-0123456789"

CLIENT = r'''
import json
import os
import sys
import time
from pathlib import Path

name = Path(sys.argv[0]).name
config_dir = Path(os.environ.get("XDG_CONFIG_HOME", "/nonexistent"))
config = config_dir / name
proc = Path("/proc/self/cmdline")
record = {
    "client": name,
    "argv": sys.argv,
    "environment": dict(os.environ),
    "proc_cmdline": proc.read_bytes().decode() if proc.exists() else None,
    "config": config.read_text() if config.exists() else None,
    "config_mode": config.stat().st_mode & 0o777 if config.exists() else None,
    "directory_mode": config_dir.stat().st_mode & 0o777 if config_dir.exists() else None,
}
target = Path(os.environ["RECORDS"]) / str(os.getpid())
pending = target.with_suffix(".tmp")
pending.write_text(json.dumps(record))
pending.rename(target.with_suffix(".json"))
if name == "mosquitto_sub":
    print("on", flush=True)
    time.sleep(30)
'''


class GreenhouseCredentialsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clients = root / "bin"
            clients.mkdir()
            records = root / "records"
            records.mkdir()
            secret = root / "password"
            secret.write_text(PASSWORD + "\n")
            secret.chmod(0o600)
            for name in ("mosquitto_pub", "mosquitto_sub"):
                client = clients / name
                client.write_text(f"#!{sys.executable}\n" + CLIENT)
                client.chmod(0o700)
            # Only relocate container paths. Exercise the real script's control
            # flow, credential handling and commands with an inherited 022 umask.
            source = SCRIPT.read_text().replace("/run/secrets/mqtt-device-password", str(secret))
            source = source.replace("/tmp/greenhouse.XXXXXX", str(root / "greenhouse.XXXXXX"))
            source = source.replace("/tmp/fan", str(root / "fan"))
            script = root / "greenhouse.sh"
            script.write_text(source)
            env = {
                **os.environ,
                "PATH": f"{clients}:{os.environ['PATH']}",
                "RECORDS": str(records),
                "INTERVAL": "0.1",
            }
            process = subprocess.Popen(
                ["/bin/sh", str(script)], env=env, start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o022,
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    cls.records = [json.loads(p.read_text()) for p in records.glob("*.json")]
                    fan_on = any(
                        "sensors/greenhouse/fan" in r["argv"] and "on" in r["argv"]
                        for r in cls.records
                    )
                    if fan_on or process.poll() is not None:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("demo never published the received fan command")
                if process.poll() is not None:
                    raise AssertionError(f"demo exited early: {process.communicate()!r}")
            finally:
                os.killpg(process.pid, signal.SIGTERM)
                cls.stdout, cls.stderr = process.communicate(timeout=5)

    def test_no_password_in_arguments_or_environment(self):
        self.assertEqual({r["client"] for r in self.records}, {"mosquitto_pub", "mosquitto_sub"})
        for record in self.records:
            with self.subTest(client=record["client"]):
                self.assertNotIn(PASSWORD, json.dumps(record["argv"]))
                self.assertNotIn("-P", record["argv"])
                self.assertNotIn(PASSWORD, json.dumps(record["environment"]))
                if record["proc_cmdline"] is not None:
                    self.assertNotIn(PASSWORD, record["proc_cmdline"])
        self.assertNotIn(PASSWORD.encode(), self.stdout + self.stderr)

    def test_both_clients_get_private_configuration(self):
        for record in self.records:
            with self.subTest(client=record["client"]):
                self.assertEqual(record["config"], f"-P {PASSWORD}\n")
                self.assertEqual(record["config_mode"], 0o600)
                self.assertEqual(record["directory_mode"], 0o700)

    def test_topics_tls_and_fan_command_are_preserved(self):
        topics = set()
        for record in self.records:
            args = record["argv"]
            self.assertEqual(args[args.index("-h") + 1], "mosquitto")
            self.assertEqual(args[args.index("-p") + 1], "8883")
            self.assertEqual(args[args.index("--cafile") + 1], "/etc/ming/certs/ca.crt")
            self.assertEqual(args[args.index("-u") + 1], "device")
            topics.add(args[args.index("-t") + 1])
        self.assertEqual(topics, {
            "actuators/fan", "sensors/greenhouse/temperature",
            "sensors/greenhouse/humidity", "sensors/greenhouse/fan",
        })
        self.assertTrue(any(
            "sensors/greenhouse/fan" in r["argv"] and "on" in r["argv"] and "-r" in r["argv"]
            for r in self.records
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
