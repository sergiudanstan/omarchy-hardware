"""Run bootstrap preflight in a throwaway stack; never run Docker or generate secrets."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("unsafe", ["loose", "symlink", "fifo"])
def test_bootstrap_secures_existing_paths_before_generation(tmp_path, unsafe):
    script = tmp_path / "bootstrap.sh"
    shutil.copyfile(ROOT / "examples/ming-stack/bootstrap.sh", script)
    bins = tmp_path / "bin"
    bins.mkdir()
    # Stop at the first certificate-generation call, after the real preflight.
    for name, body in (("docker", "exit 0"), ("setfacl", 'printf "%s\\n" "$*" >> "$ACL_LOG"'),
                       ("openssl", "exit 42")):
        path = bins / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o700)
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o755)
    outside = tmp_path / "outside"
    outside.write_text("public fixture")
    outside.chmod(0o644)
    credential = directory / "influxdb-admin-token"
    if unsafe == "symlink":
        credential.symlink_to(outside)
    elif unsafe == "fifo":
        os.mkfifo(credential, 0o600)
    else:
        credential.write_text("public fixture")
        credential.chmod(0o644)
    log = tmp_path / "acl.log"
    result = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=5,  # noqa: S603
                            env=dict(os.environ, PATH=f"{bins}:{os.environ['PATH']}", ACL_LOG=str(log)))
    assert outside.read_text() == "public fixture"
    assert outside.stat().st_mode & 0o777 == 0o644
    if unsafe == "loose":
        assert result.returncode == 42, result.stderr
        assert directory.stat().st_mode & 0o777 == 0o700
        assert credential.stat().st_mode & 0o777 == 0o600
        assert "-b -k secrets" in log.read_text()
        assert "-b secrets/influxdb-admin-token" in log.read_text()
    else:
        assert result.returncode == 1
        assert "unsafe private file" in result.stderr
