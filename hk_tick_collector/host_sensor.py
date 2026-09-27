"""Host-level resource sensing for burstable-instance early warning.

The collector's existing health checks all describe the ingestion pipeline:
queue depth, write throughput, symbol staleness, timestamp drift.  Every one of
them reported OK for the whole of the 2026-08-17 incident, during which the
host exhausted its CPU credit balance and was throttled by the hypervisor to a
fraction of its nominal 2 vCPU.  The collector kept ingesting at 53 rows/sec;
the machine died around it, and the health digest would have said
"正常：盤中採集與寫入穩定".

This module adds the missing dimension.  It reads /proc directly -- no new
dependency -- and is cheap enough to run on every 60s health tick.

The important design choice is *what* to alert on.  Steal is a confirmation
signal, not a warning: once steal is high the credit balance is already gone
and takes hours to rebuild, so the only remaining move is to shed load.  The
leading signal is sustained utilisation above the instance's sustainable
baseline.  On 2026-08-17 the timeline was:

    01:10Z  utilisation 14% -> 50%, credits start draining
    01:30Z  <- sustained-burn alert would fire here
    03:10Z  steal 3.3% -> 10%
    03:20Z  steal -> 66%, service effectively dead
            <- a steal threshold would fire only here, with zero lead time

Deliberately not modelled: a countdown to credit exhaustion.  The balance is
not readable from inside the instance and is unknown at process start, so any
"exhausted in N minutes" figure would be invented.  What is reported instead --
how long we have been over baseline and how much has been overdrawn since --
is exactly measurable.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Sequence

logger = logging.getLogger(__name__)

PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")
PROC_PRESSURE_MEMORY = Path("/proc/pressure/memory")
PROC_VMSTAT = Path("/proc/vmstat")
PROC_ROOT = Path("/proc")

# Rolling window length.  At the 60s health cadence this holds ~15 minutes,
# enough for the 10-minute averages the thresholds are defined over.
_WINDOW_SAMPLES = 16

# A process must move at least this many CPU-seconds between samples before it
# is worth naming in an alert; filters out the long tail of idle daemons.
_MIN_PROC_CPU_SEC = 0.05


def _read_first_line(path: Path) -> str | None:
    try:
        with path.open("r") as handle:
            return handle.readline()
    except OSError:
        return None


@dataclass(frozen=True)
class CpuTimes:
    """Aggregate jiffies from /proc/stat, in field order."""

    user: int
    nice: int
    system: int
    idle: int
    iowait: int
    irq: int
    softirq: int
    steal: int

    @property
    def total(self) -> int:
        return (
            self.user
            + self.nice
            + self.system
            + self.idle
            + self.iowait
            + self.irq
            + self.softirq
            + self.steal
        )

    @property
    def busy(self) -> int:
        """CPU time this guest actually consumed.

        Excludes idle/iowait (not consumed) and steal (wanted but not granted).
        This is the quantity a burstable instance charges credits against.
        """

        return self.user + self.nice + self.system + self.irq + self.softirq


def read_cpu_times() -> CpuTimes | None:
    line = _read_first_line(PROC_STAT)
    if not line or not line.startswith("cpu "):
        return None
    parts = line.split()
    try:
        values = [int(value) for value in parts[1:9]]
    except (ValueError, IndexError):
        return None
    while len(values) < 8:
        values.append(0)
    return CpuTimes(*values[:8])


@dataclass(frozen=True)
class ProcessCpu:
    """A process's share of the CPU the box actually consumed.

    Not derived from wall-clock capacity. On a throttled guest, per-task
    utime/stime and the global /proc/stat disagree badly: the scheduler counts
    a task as running across a slice the hypervisor stole, so raw per-process
    ticks over wall time can exceed the global busy total several-fold --
    measured at 3.7x on this host at 64% steal. Since the inflation is shared
    across all tasks, the *ranking* survives; the magnitude is restored by
    normalising against the summed process ticks and rescaling to the global
    utilisation, which keeps `top_processes` consistent with `cpu_util_pct`.
    """

    pid: int
    name: str
    cpu_pct_of_box: float


def _read_process_cpu_ticks() -> dict[int, tuple[str, int]]:
    """Map pid -> (comm, utime+stime) for every readable process."""

    out: dict[int, tuple[str, int]] = {}
    try:
        entries = os.listdir(PROC_ROOT)
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with (PROC_ROOT / entry / "stat").open("r") as handle:
                raw = handle.read()
        except OSError:
            continue
        # comm is parenthesised and may itself contain spaces/parens, so split
        # on the *last* ')' rather than tokenising the whole line.
        close = raw.rfind(")")
        open_paren = raw.find("(")
        if close < 0 or open_paren < 0:
            continue
        name = raw[open_paren + 1 : close]
        fields = raw[close + 2 :].split()
        try:
            utime = int(fields[11])
            stime = int(fields[12])
        except (ValueError, IndexError):
            continue
        out[int(entry)] = (name, utime + stime)
    return out


def _read_swap_mb() -> tuple[float | None, float | None]:
    """Return (swap_total_mb, swap_used_mb)."""

    total_kb: float | None = None
    free_kb: float | None = None
    try:
        with PROC_MEMINFO.open("r") as handle:
            for line in handle:
                if line.startswith("SwapTotal:"):
                    total_kb = float(line.split()[1])
                elif line.startswith("SwapFree:"):
                    free_kb = float(line.split()[1])
                if total_kb is not None and free_kb is not None:
                    break
    except (OSError, ValueError, IndexError):
        return None, None
    if total_kb is None or free_kb is None:
        return None, None
    return total_kb / 1024.0, (total_kb - free_kb) / 1024.0


def _read_swap_events() -> tuple[int | None, int | None]:
    """Cumulative pages swapped in/out, from /proc/vmstat.

    Absolute swap *occupancy* is a poor pressure signal: pages parked during a
    past incident stay resident for days without costing anything. What hurts
    is churn, so the alert is driven from the delta of these counters.
    """

    pswpin: int | None = None
    pswpout: int | None = None
    try:
        with PROC_VMSTAT.open("r") as handle:
            for line in handle:
                if line.startswith("pswpin "):
                    pswpin = int(line.split()[1])
                elif line.startswith("pswpout "):
                    pswpout = int(line.split()[1])
                if pswpin is not None and pswpout is not None:
                    break
    except (OSError, ValueError, IndexError):
        return None, None
    return pswpin, pswpout


def _read_memory_pressure_full_avg60() -> float | None:
    """PSI: share of time *every* task was stalled on memory over 60s.

    `full` is the honest thrashing signal; `some` fires on ordinary reclaim.
    Absent on kernels built without CONFIG_PSI.
    """

    try:
        with PROC_PRESSURE_MEMORY.open("r") as handle:
            for line in handle:
                if not line.startswith("full"):
                    continue
                for token in line.split():
                    if token.startswith("avg60="):
                        return float(token.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def _read_disk(path: Path) -> tuple[float | None, float | None, float | None]:
    """Return (used_pct, free_gb, total_gb) for the filesystem holding path."""

    try:
        stat = os.statvfs(path)
    except OSError:
        return None, None, None
    total = stat.f_blocks * stat.f_frsize
    if total <= 0:
        return None, None, None
    free = stat.f_bavail * stat.f_frsize
    # Match `df`: capacity is measured against what is usable by non-root.
    usable = stat.f_bavail + (stat.f_blocks - stat.f_bfree)
    used_pct = 100.0 * (stat.f_blocks - stat.f_bfree) / usable if usable > 0 else None
    return used_pct, free / (1024.0**3), total / (1024.0**3)


@dataclass(frozen=True)
class HostSnapshot:
    """Derived host state, safe to render or evaluate thresholds against."""

    created_at: float
    ncpu: int
    baseline_pct: float

    # Instantaneous (last inter-sample interval).
    cpu_util_pct: float | None = None
    cpu_steal_pct: float | None = None
    load1: float | None = None

    # Rolling means over the window (~10 min at the 60s cadence).
    cpu_util_pct_avg: float | None = None
    cpu_steal_pct_avg: float | None = None

    # Credit burn accounting.  over_baseline_sec counts contiguous time with
    # the rolling mean above baseline; overdraft is the integral of the excess.
    over_baseline_sec: float = 0.0
    overdraft_vcpu_min: float = 0.0

    top_processes: Sequence[ProcessCpu] = field(default_factory=tuple)

    disk_used_pct: float | None = None
    disk_free_gb: float | None = None
    disk_days_to_full: float | None = None

    swap_used_mb: float | None = None
    swap_total_mb: float | None = None
    swap_pages_per_sec: float | None = None
    mem_pressure_full_avg60: float | None = None

    def cpu_line(self) -> str:
        util = "n/a" if self.cpu_util_pct_avg is None else f"{self.cpu_util_pct_avg:.0f}%"
        steal = "n/a" if self.cpu_steal_pct_avg is None else f"{self.cpu_steal_pct_avg:.0f}%"
        load = "n/a" if self.load1 is None else f"{self.load1:.2f}"
        return (
            f"util={util} baseline={self.baseline_pct:.0f}% "
            f"steal={steal} load={load}/{self.ncpu}core"
        )

    def top_line(self, limit: int = 3) -> str:
        if not self.top_processes:
            return "Top: n/a"
        parts = [f"{proc.name} {proc.cpu_pct_of_box:.1f}%" for proc in self.top_processes[:limit]]
        return "Top: " + "  ".join(parts)


class HostSensor:
    """Samples /proc and maintains the rolling windows the thresholds need.

    One instance per process.  `sample()` is expected to be called on a fixed
    cadence (the collector's 60s health tick); every derived rate is computed
    from the delta against the previous call, so the first sample only primes
    the state and returns a snapshot with no rate fields populated.
    """

    def __init__(
        self,
        *,
        ncpu: int | None = None,
        baseline_pct: float = 20.0,
        disk_path: Path | str = "/",
        window_samples: int = _WINDOW_SAMPLES,
    ) -> None:
        self._ncpu = ncpu or (os.cpu_count() or 1)
        self._baseline_pct = max(0.0, min(100.0, float(baseline_pct)))
        self._disk_path = Path(disk_path)
        self._window = max(2, int(window_samples))

        self._prev_cpu: CpuTimes | None = None
        self._prev_procs: dict[int, tuple[str, int]] = {}
        self._prev_at: float | None = None

        self._util_window: Deque[float] = deque(maxlen=self._window)
        self._steal_window: Deque[float] = deque(maxlen=self._window)
        self._disk_window: Deque[tuple[float, float]] = deque(maxlen=self._window)

        self._over_baseline_since: float | None = None
        self._overdraft_vcpu_min = 0.0
        self._prev_swap_events: tuple[int | None, int | None] = (None, None)

    @property
    def baseline_pct(self) -> float:
        return self._baseline_pct

    def sample(self, *, now: float | None = None) -> HostSnapshot:
        now = time.time() if now is None else now
        cpu = read_cpu_times()
        procs = _read_process_cpu_ticks()
        dt = None if self._prev_at is None else max(1e-6, now - self._prev_at)

        util_pct: float | None = None
        steal_pct: float | None = None
        if cpu is not None and self._prev_cpu is not None:
            total_delta = cpu.total - self._prev_cpu.total
            if total_delta > 0:
                util_pct = 100.0 * (cpu.busy - self._prev_cpu.busy) / total_delta
                steal_pct = 100.0 * (cpu.steal - self._prev_cpu.steal) / total_delta
                self._util_window.append(util_pct)
                self._steal_window.append(steal_pct)

        top = self._top_processes(procs, dt, util_pct)
        util_avg = self._mean(self._util_window)
        self._accumulate_overdraft(util_avg, dt, now)

        disk_used_pct, disk_free_gb, _total_gb = _read_disk(self._disk_path)
        if disk_used_pct is not None:
            self._disk_window.append((now, disk_used_pct))
        swap_total_mb, swap_used_mb = _read_swap_mb()
        swap_events = _read_swap_events()
        swap_pages_per_sec: float | None = None
        if dt is not None and None not in swap_events and None not in self._prev_swap_events:
            moved = (swap_events[0] - self._prev_swap_events[0]) + (
                swap_events[1] - self._prev_swap_events[1]
            )
            swap_pages_per_sec = max(0.0, moved / dt)
        self._prev_swap_events = swap_events

        try:
            load1: float | None = os.getloadavg()[0]
        except (AttributeError, OSError):
            load1 = None

        self._prev_cpu = cpu or self._prev_cpu
        self._prev_procs = procs or self._prev_procs
        self._prev_at = now

        return HostSnapshot(
            created_at=now,
            ncpu=self._ncpu,
            baseline_pct=self._baseline_pct,
            cpu_util_pct=util_pct,
            cpu_steal_pct=steal_pct,
            load1=load1,
            cpu_util_pct_avg=util_avg,
            cpu_steal_pct_avg=self._mean(self._steal_window),
            over_baseline_sec=self._over_baseline_seconds(now),
            overdraft_vcpu_min=self._overdraft_vcpu_min,
            top_processes=top,
            disk_used_pct=disk_used_pct,
            disk_free_gb=disk_free_gb,
            disk_days_to_full=self._disk_days_to_full(disk_used_pct),
            swap_used_mb=swap_used_mb,
            swap_total_mb=swap_total_mb,
            swap_pages_per_sec=swap_pages_per_sec,
            mem_pressure_full_avg60=_read_memory_pressure_full_avg60(),
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _mean(values: Sequence[float] | Deque[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    def _top_processes(
        self,
        procs: dict[int, tuple[str, int]],
        dt: float | None,
        util_pct: float | None,
    ) -> tuple[ProcessCpu, ...]:
        """Rank processes and express each as a share of the box.

        See ProcessCpu for why the raw ticks cannot be divided by wall-clock
        capacity on a throttled guest.
        """

        if dt is None or not procs or not self._prev_procs:
            return ()
        try:
            hz = os.sysconf("SC_CLK_TCK") or 100
        except (ValueError, OSError):
            hz = 100
        deltas: list[tuple[int, str, int]] = []
        for pid, (name, ticks) in procs.items():
            previous = self._prev_procs.get(pid)
            if previous is None:
                continue
            delta = ticks - previous[1]
            if delta <= 0 or delta / hz < _MIN_PROC_CPU_SEC:
                continue
            deltas.append((delta, pid, name))
        if not deltas:
            return ()
        total_delta = sum(item[0] for item in deltas)
        if total_delta <= 0:
            return ()
        # Rescale to the global utilisation so the parts stay consistent with
        # the whole. Falls back to wall-clock capacity only when utilisation is
        # unavailable (first sample), where the two agree anyway.
        if util_pct is None:
            capacity_ticks = dt * hz * self._ncpu
            scale = (100.0 / capacity_ticks) if capacity_ticks > 0 else 0.0
        else:
            scale = util_pct / total_delta
        scored = [
            ProcessCpu(pid=pid, name=name, cpu_pct_of_box=delta * scale)
            for delta, pid, name in deltas
        ]
        scored.sort(key=lambda item: item.cpu_pct_of_box, reverse=True)
        return tuple(scored[:5])

    def _accumulate_overdraft(self, util_avg: float | None, dt: float | None, now: float) -> None:
        if util_avg is None:
            return
        excess = util_avg - self._baseline_pct
        if excess > 0:
            if self._over_baseline_since is None:
                self._over_baseline_since = now
                self._overdraft_vcpu_min = 0.0
            if dt is not None:
                # vCPU-minutes overdrawn: excess share of the whole box, over dt.
                self._overdraft_vcpu_min += (excess / 100.0) * self._ncpu * (dt / 60.0)
        else:
            self._over_baseline_since = None
            self._overdraft_vcpu_min = 0.0

    def _over_baseline_seconds(self, now: float) -> float:
        if self._over_baseline_since is None:
            return 0.0
        return max(0.0, now - self._over_baseline_since)

    def _disk_days_to_full(self, used_pct: float | None) -> float | None:
        """Linear projection from the oldest sample in the window.

        Returns None when the trend is flat or shrinking; a projection is only
        worth showing when the number is going the wrong way.
        """

        if used_pct is None or len(self._disk_window) < 2:
            return None
        oldest_at, oldest_pct = self._disk_window[0]
        newest_at, newest_pct = self._disk_window[-1]
        elapsed = newest_at - oldest_at
        if elapsed < 60.0:
            return None
        growth_pct_per_day = (newest_pct - oldest_pct) / elapsed * 86400.0
        if growth_pct_per_day <= 0.01:
            return None
        return max(0.0, (100.0 - newest_pct) / growth_pct_per_day)
