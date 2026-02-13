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


def test_hk_tickctl_doctor_detects_v22_schema(tmp_path: Path) -> None:
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
Feb 13 01:00:00 host hk-tick-collector[1]: telegram_notifier_started notify_schema=v2.2 version=0.1.0
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
    assert "notify_schema=v2.2" in result.stdout
    assert "[PASS] deployment likely on notify_schema=v2.2+ (sid present)" in result.stdout


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


def test_hk_tickctl_status_reports_health_and_alert(tmp_path: Path) -> None:
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
Feb 13 01:01:00 host hk-tick-collector[1]: health sid=sid-a1 connected=True queue=0/50000 persisted_rows_per_min=12000 phase=open symbols=1000
Feb 13 01:02:00 host hk-tick-collector[1]: telegram_enqueue kind=DISCONNECT severity=ALERT fingerprint=DISCONNECT reason=new eid=eid-x1 sid=sid-a1
LOG
""",
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), "status", "--since", "30 minutes ago"],
        cwd=str(ROOT),
        env=_base_env(bin_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "hk-tickctl status" in result.stdout
    assert "latest health" in result.stdout
    assert "latest alert/watchdog" in result.stdout


def test_hk_tickctl_db_symbol_invokes_sqlite3(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    db_path = tmp_path / "20260213.db"
    db_path.write_text("", encoding="utf-8")
    _write_executable(
        bin_dir / "sqlite3",
        """#!/usr/bin/env bash
echo "symbol|ts_utc|price|volume|turnover|seq"
echo "HK.00700|2026-02-13 09:30:00|350.2|100|35020.0|1"
""",
    )
    env = _base_env(bin_dir)
    env["DATA_ROOT"] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPT), "db", "symbol", "HK.00700", "--db", str(db_path), "--last", "5"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "HK.00700" in result.stdout
