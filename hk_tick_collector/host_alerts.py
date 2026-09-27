"""Threshold state machine turning host samples into alert transitions.

Kept free of any notifier dependency so it can be unit-tested against
synthetic `HostSnapshot` sequences: `evaluate()` is a pure function of the
snapshot plus the evaluator's own state, and returns the transitions the
caller should dispatch.

Alert vocabulary (all new; the pre-existing codes -- DISCONNECT,
PERSIST_STALL, RESTART, SQLITE_BUSY, SYMBOL_UNAVAILABLE -- describe the
ingestion pipeline only):

    HOST_CPU_BURN        WARN   leading: sustained above the credit baseline
    HOST_CPU_THROTTLED   ALERT  confirming: the hypervisor is capping us
    HOST_DISK            WARN/ALERT
    HOST_MEM_PRESSURE    WARN

Every code fires once on entry and resolves once on exit.  Nothing here is
periodic: the health digest already carries a heartbeat, and a second stream
of "still fine" messages is how alerting gets ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Sequence

from .host_sensor import HostSnapshot

logger = logging.getLogger(__name__)

Tier = Literal["warn", "alert"]


@dataclass(frozen=True)
class HostAlertThresholds:
    """All tunables in one place; every field is env-overridable via Config."""

    # Leading CPU signal.  `sustain_sec` is what buys the lead time: a brief
    # spike above baseline is normal burst behaviour and must not page.
    cpu_burn_sustain_sec: int = 1200
    # Confirming CPU signal.  Steal is noisy at the low end on shared hosts,
    # so the bar is set well clear of background noise (~0.5% when healthy).
    cpu_steal_alert_pct: float = 15.0

    disk_warn_pct: float = 80.0
    disk_alert_pct: float = 90.0

    # PSI "full" = share of wall time during which *every* task was stalled on
    # memory. Anything sustained above a few percent is real thrashing.
    mem_pressure_full_avg60: float = 5.0
    # Swap *churn*, not occupancy. Absolute swap-used is a false-positive
    # factory: 611MB left parked after an incident sat there for hours on this
    # host with PSI flat at 0.0 -- nothing was wrong, the pages were simply
    # cold. Paging traffic is what actually costs latency.
    swap_pages_per_sec_warn: float = 200.0


@dataclass(frozen=True)
class HostAlertTransition:
    """One thing for the caller to send."""

    action: Literal["fire", "resolve"]
    code: str
    fingerprint: str
    severity: str  # NotifySeverity value; unused for resolves
    headline: str = ""
    impact: str = ""
    summary_lines: Sequence[str] = field(default_factory=tuple)
    suggestions: Sequence[str] = field(default_factory=tuple)


def _fmt(value: float | None, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} 分鐘"
    return f"{minutes // 60} 小時 {minutes % 60} 分鐘"


class HostAlertEvaluator:
    def __init__(self, thresholds: HostAlertThresholds | None = None) -> None:
        self._t = thresholds or HostAlertThresholds()
        # code -> currently-active tier
        self._active: dict[str, Tier] = {}

    @property
    def thresholds(self) -> HostAlertThresholds:
        return self._t

    @property
    def active_codes(self) -> tuple[str, ...]:
        """Codes currently held active, for the health digest to reflect."""

        return tuple(sorted(self._active))

    def evaluate(self, snapshot: HostSnapshot) -> list[HostAlertTransition]:
        out: list[HostAlertTransition] = []
        for desired in (
            self._eval_cpu_burn(snapshot),
            self._eval_cpu_throttled(snapshot),
            self._eval_disk(snapshot),
            self._eval_mem(snapshot),
        ):
            if desired is None:
                continue
            code, tier, payload = desired
            out.extend(self._reconcile(code, tier, payload))
        return out

    # -- transition bookkeeping -------------------------------------------

    def _reconcile(
        self,
        code: str,
        tier: Tier | None,
        payload: HostAlertTransition | None,
    ) -> list[HostAlertTransition]:
        previous = self._active.get(code)
        if tier is None:
            if previous is None:
                return []
            del self._active[code]
            return [
                HostAlertTransition(
                    action="resolve",
                    code=code,
                    fingerprint=f"{code}:{previous}",
                    severity="",
                    summary_lines=("status=recovered",),
                )
            ]
        if previous == tier or payload is None:
            return []
        self._active[code] = tier
        events: list[HostAlertTransition] = []
        if previous is not None:
            # Tier changed (e.g. disk warn -> alert). Close the old fingerprint
            # so the recovered notice for it is not left dangling.
            events.append(
                HostAlertTransition(
                    action="resolve",
                    code=code,
                    fingerprint=f"{code}:{previous}",
                    severity="",
                    summary_lines=(f"status=escalated_to_{tier}",),
                )
            )
        events.append(payload)
        return events

    # -- individual rules --------------------------------------------------

    def _eval_cpu_burn(
        self, s: HostSnapshot
    ) -> tuple[str, Tier | None, HostAlertTransition | None] | None:
        code = "HOST_CPU_BURN"
        if s.cpu_util_pct_avg is None:
            return None
        sustained = s.over_baseline_sec >= self._t.cpu_burn_sustain_sec
        if not sustained:
            return (code, None, None)
        payload = HostAlertTransition(
            action="fire",
            code=code,
            fingerprint=f"{code}:warn",
            severity="WARN",
            headline="注意：主機 CPU 持續高於可持續基線",
            impact=(
                "突發額度正在淨消耗。額度耗盡後 hypervisor 會限流到基線，"
                "屆時服務會整體變慢，且額度需數小時才能回補"
            ),
            summary_lines=(
                f"持續 {_duration(s.over_baseline_sec)}｜期間透支 ≈ "
                f"{_fmt(s.overdraft_vcpu_min, 1)} vCPU-分鐘",
                s.cpu_line(),
                s.top_line(),
            ),
            suggestions=(
                "先確認上面 Top 進程是否為預期負載",
                "若非預期，降載或停掉該服務即可讓額度回補",
                "mpstat 2 5   # steal 若開始上升代表額度已見底",
            ),
        )
        return (code, "warn", payload)

    def _eval_cpu_throttled(
        self, s: HostSnapshot
    ) -> tuple[str, Tier | None, HostAlertTransition | None] | None:
        code = "HOST_CPU_THROTTLED"
        if s.cpu_steal_pct_avg is None:
            return None
        if s.cpu_steal_pct_avg < self._t.cpu_steal_alert_pct:
            return (code, None, None)
        payload = HostAlertTransition(
            action="fire",
            code=code,
            fingerprint=f"{code}:alert",
            severity="ALERT",
            headline="警告：主機已被限流（CPU 額度耗盡）",
            impact=(
                "實際可用 CPU 已遠低於標稱值，所有服務都會變慢；"
                "額度只有在用量降到基線以下後才會回補"
            ),
            summary_lines=(
                s.cpu_line(),
                s.top_line(),
                f"透支累計 ≈ {_fmt(s.overdraft_vcpu_min, 1)} vCPU-分鐘",
            ),
            suggestions=(
                "立即降載：停掉 Top 進程中非必要的服務",
                "此時調參數無效，必須先讓額度回補",
                "考慮升級機型或改用非 burstable 實例",
            ),
        )
        return (code, "alert", payload)

    def _eval_disk(
        self, s: HostSnapshot
    ) -> tuple[str, Tier | None, HostAlertTransition | None] | None:
        code = "HOST_DISK"
        if s.disk_used_pct is None:
            return None
        if s.disk_used_pct >= self._t.disk_alert_pct:
            tier: Tier = "alert"
            severity = "ALERT"
            headline = "警告：磁碟即將寫滿"
        elif s.disk_used_pct >= self._t.disk_warn_pct:
            tier = "warn"
            severity = "WARN"
            headline = "注意：磁碟使用率偏高"
        else:
            return (code, None, None)
        projection = (
            f"按目前增速約 {s.disk_days_to_full:.1f} 天寫滿"
            if s.disk_days_to_full is not None
            else "目前無明顯增長趨勢"
        )
        payload = HostAlertTransition(
            action="fire",
            code=code,
            fingerprint=f"{code}:{tier}",
            severity=severity,
            headline=headline,
            impact="磁碟寫滿會讓 SQLite 寫入失敗並中斷採集",
            summary_lines=(
                f"used={_fmt(s.disk_used_pct, 1, '%')} free={_fmt(s.disk_free_gb, 1, 'GB')}",
                projection,
            ),
            suggestions=(
                "df -h / && du -shx /var/lib/containerd /var/log",
                "docker system df   # 建置快取與舊映像常是大頭",
                "journalctl --disk-usage",
            ),
        )
        return (code, tier, payload)

    def _eval_mem(
        self, s: HostSnapshot
    ) -> tuple[str, Tier | None, HostAlertTransition | None] | None:
        code = "HOST_MEM_PRESSURE"
        psi = s.mem_pressure_full_avg60
        swap_rate = s.swap_pages_per_sec
        if psi is None and swap_rate is None:
            return None
        psi_hit = psi is not None and psi >= self._t.mem_pressure_full_avg60
        swap_hit = swap_rate is not None and swap_rate >= self._t.swap_pages_per_sec_warn
        if not (psi_hit or swap_hit):
            return (code, None, None)
        payload = HostAlertTransition(
            action="fire",
            code=code,
            fingerprint=f"{code}:warn",
            severity="WARN",
            headline="注意：主機記憶體壓力偏高",
            impact=(
                "page cache 被反覆驅逐會讓 SQLite 查詢由記憶體命中退化成真實讀盤，"
                "延遲可能放大一個數量級"
            ),
            summary_lines=(
                f"PSI(memory full avg60)={_fmt(psi, 2, '%')}｜"
                f"換頁 {_fmt(swap_rate, 0, ' pages/s')}｜"
                f"swap 佔用 {_fmt(s.swap_used_mb, 0, 'MB')}/{_fmt(s.swap_total_mb, 0, 'MB')}",
                s.top_line(),
            ),
            suggestions=(
                "free -h && cat /proc/pressure/memory",
                "檢查是否有容器 mem_limit 低於實際工作集",
            ),
        )
        return (code, "warn", payload)
