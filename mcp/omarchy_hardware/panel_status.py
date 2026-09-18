"""One JSON line describing configured targets and the audit log, for the bar panel.

Offline by design: it reads config.toml and audit.log and contacts nothing. The
panel runs it when opened, so reachability probes (SSH, MQTT, HTTP) would put
network traffic behind a click on the bar. Hostnames appear here because this
output goes to the user's own panel, not to the model.

Standard library only, like boards.py: it must work before setup.sh has built
the virtualenv.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import audit
from .config import Config, ConfigError
from .config import load as load_config


def _targets(config: Config) -> dict[str, Any]:
    return {
        "pi": list(config.pi_hosts),
        "jetson": list(config.jetson_hosts),
        "ming": {
            "allow": config.ming_allow,
            "mqtt": [broker.name for broker in config.ming_mqtt],
            "influxdb": [db.name for db in config.ming_influxdb],
            "nodered": [nodered.name for nodered in config.ming_nodered],
            "grafana": [grafana.name for grafana in config.ming_grafana],
        },
        "weintek": {
            "allow": config.weintek_allow,
            "opcua": len(config.weintek_opcua),
            "mqtt": [f"{target.host}:{target.port}" for target in config.weintek_mqtt],
        },
        "flash": config.allow_flash,
    }


def status() -> dict[str, Any]:
    try:
        config = load_config()
    except ConfigError as exc:
        config_error: str | None = str(exc)
        targets = None
    else:
        config_error = None
        targets = _targets(config)

    try:
        log = audit.verify()
    except OSError as exc:
        log = {"ok": False, "records": 0, "reason": f"cannot read the log ({type(exc).__name__})"}
    log.pop("path", None)

    return {"ok": config_error is None, "config_error": config_error, "targets": targets, "audit": log}


def main() -> None:
    json.dump(status(), sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
