import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hk-tickctl"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _base_env(bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["SERVICE_NAME"] = "hk-tick-collector"
    return env


def test_hk_tickctl_doctor_detects_v21_schema(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "systemctl",
        """#!/usr/bin/env bash
if [[ "$1" == "is-active" ]]; then
  echo "active"
  exit 0
fi
echo "unknown"
""",
    )
    _write_executable(
        bin_dir / "journalctl",
        """#!/usr/bin/env bash
cat <<'LOG'
Feb 13 01:00:00 host hk-tick-collector[1]: telegram_notifier_started notify_schema=v2.1 version=0.1.0
Feb 13 01:01:00 host hk-tick-collector[1]: telegram_enqueue kind=HEALTH severity=OK fingerprint=HEALTH:20260213:open reason=bootstrap eid=none sid=sid-11112222
Feb 13 01:02:00 host hk-tick-collector[1]: telegram_enqueue kind=PERSIST_STALL severity=ALERT fingerprint=PERSIST_STALL reason=new eid=eid-aabbccdd sid=sid-11112222
LOG
""",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT), "doctor", "--since", "2 hours ago"],
        cwd=str(ROOT),
        env=_base_env(bin_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "[OK] found notify schema line" in result.stdout
    assert "notify_schema=v2.1" in result.stdout
    assert "[PASS] deployment likely on notify_schema=v2.1+ (sid present)" in result.stdout


def test_hk_tickctl_doctor_reports_possible_old_deployment(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "systemctl",
        """#!/usr/bin/env bash
if [[ "$1" == "is-active" ]]; then
  echo "active"
  exit 0
fi
echo "unknown"
""",
    )
    _write_executable(
        bin_dir / "journalctl",
        """#!/usr/bin/env bash
cat <<'LOG'
Feb 13 01:01:00 host hk-tick-collector[1]: telegram_enqueue kind=HEALTH severity=OK fingerprint=HEALTH:20260213:open reason=bootstrap eid=none
LOG
""",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT), "doctor", "--since", "2 hours ago"],
        cwd=str(ROOT),
        env=_base_env(bin_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "[WARN] notify schema line missing" in result.stdout
    assert "[CHECK] deployment may be old or not fully enabled" in result.stdout
