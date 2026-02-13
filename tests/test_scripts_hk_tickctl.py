import os
import subprocess
import tarfile
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


def test_hk_tickctl_doctor_detects_notify_schema(tmp_path: Path) -> None:
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
    assert "=== HK Tick 版本診斷 ===" in result.stdout
    assert "找到 notify schema" in result.stdout


def test_hk_tickctl_doctor_reports_missing_schema(tmp_path: Path) -> None:
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
Feb 13 01:01:00 host hk-tick-collector[1]: health sid=sid-aaa queue=0/50000
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
    assert "沒找到 notify schema" in result.stdout


def test_hk_tickctl_status_reports_health_and_alert(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "systemctl",
        """#!/usr/bin/env bash
if [[ "$1" == "is-active" ]]; then
  if [[ "$2" == "futu-opend" ]]; then
    echo "active"
  else
    echo "active"
  fi
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
    assert "=== HK Tick 狀態 ===" in result.stdout
    assert "[健康]" in result.stdout
    assert "[告警]" in result.stdout


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


def test_hk_tickctl_export_creates_tar_and_checksum(tmp_path: Path) -> None:
    db_path = tmp_path / "20260213.db"
    db_path.write_text("mock db", encoding="utf-8")

    out_path = tmp_path / "export" / "hk-20260213.tar.gz"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "export",
            "--db",
            str(db_path),
            "--out",
            str(out_path),
        ],
        cwd=str(ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert out_path.exists()
    assert Path(f"{out_path}.sha256").exists()

    with tarfile.open(out_path, "r:gz") as tar:
        names = tar.getnames()
    assert "20260213.db" in names


def test_hk_tickctl_tg_test_uses_python_sender(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_executable(
        bin_dir / "python3",
        """#!/usr/bin/env bash
echo "[完成] Telegram 測試訊息已送出 message_id=42"
""",
    )

    env = _base_env(bin_dir)
    env["TG_TOKEN"] = "123456:ABC"
    env["TG_CHAT_ID"] = "-100123"

    result = subprocess.run(
        ["bash", str(SCRIPT), "tg", "test"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Telegram 測試訊息已送出" in result.stdout
