"""Tests for host-level sensing and its threshold state machine.

The load-bearing test here is `test_incident_20260817_replay`, which encodes
the reason this feature exists: alerting on steal gives zero lead time, while
alerting on sustained burn would have fired roughly 110 minutes earlier.
"""

from __future__ import annotations

import pytest

from hk_tick_collector import host_sensor
from hk_tick_collector.host_alerts import HostAlertEvaluator, HostAlertThresholds
from hk_tick_collector.host_sensor import CpuTimes, HostSensor, HostSnapshot

HZ = 100
NCPU = 2


def _cpu_times(
    *, busy_pct: float, steal_pct: float, elapsed_sec: float, prev: CpuTimes
) -> CpuTimes:
    """Advance a CpuTimes by one interval at the given utilisation split."""

    total_delta = elapsed_sec * HZ * NCPU
    busy_delta = total_delta * busy_pct / 100.0
    steal_delta = total_delta * steal_pct / 100.0
    idle_delta = total_delta - busy_delta - steal_delta
    return CpuTimes(
        user=prev.user + int(busy_delta),
        nice=prev.nice,
        system=prev.system,
        idle=prev.idle + int(idle_delta),
        iowait=prev.iowait,
        irq=prev.irq,
        softirq=prev.softirq,
        steal=prev.steal + int(steal_delta),
    )


@pytest.fixture
def stub_host(monkeypatch):
    """Drive HostSensor from a scripted CPU series; stub the rest of /proc."""

    state = {
        "cpu": CpuTimes(0, 0, 0, 0, 0, 0, 0, 0),
        "disk_pct": 40.0,
        "psi": 0.0,
        "swap": 0.0,
        "swpin": 0,
        "swpout": 0,
    }

    monkeypatch.setattr(host_sensor, "read_cpu_times", lambda: state["cpu"])
    monkeypatch.setattr(host_sensor, "_read_process_cpu_ticks", lambda: {})
    monkeypatch.setattr(host_sensor, "_read_disk", lambda path: (state["disk_pct"], 10.0, 58.0))
    monkeypatch.setattr(host_sensor, "_read_swap_mb", lambda: (2048.0, state["swap"]))
    monkeypatch.setattr(host_sensor, "_read_swap_events", lambda: (state["swpin"], state["swpout"]))
    monkeypatch.setattr(host_sensor, "_read_memory_pressure_full_avg60", lambda: state["psi"])
    return state


def _advance(sensor: HostSensor, state: dict, *, busy_pct, steal_pct, seconds, t0) -> HostSnapshot:
    state["cpu"] = _cpu_times(
        busy_pct=busy_pct, steal_pct=steal_pct, elapsed_sec=seconds, prev=state["cpu"]
    )
    return sensor.sample(now=t0)


# -- sensor ---------------------------------------------------------------


def test_first_sample_only_primes(stub_host):
    sensor = HostSensor(ncpu=NCPU, baseline_pct=20.0)
    snap = sensor.sample(now=0.0)
    assert snap.cpu_util_pct is None
    assert snap.cpu_util_pct_avg is None
    # Non-rate readings are available immediately.
    assert snap.disk_used_pct == pytest.approx(40.0)


def test_utilisation_and_steal_are_derived_from_deltas(stub_host):
    sensor = HostSensor(ncpu=NCPU, baseline_pct=20.0)
    sensor.sample(now=0.0)
    snap = _advance(sensor, stub_host, busy_pct=50.0, steal_pct=10.0, seconds=60, t0=60.0)
    assert snap.cpu_util_pct == pytest.approx(50.0, abs=0.5)
    assert snap.cpu_steal_pct == pytest.approx(10.0, abs=0.5)


def test_overdraft_accumulates_only_above_baseline_and_resets(stub_host):
    sensor = HostSensor(ncpu=NCPU, baseline_pct=20.0)
    sensor.sample(now=0.0)
    t = 60.0
    for _ in range(10):
        snap = _advance(sensor, stub_host, busy_pct=50.0, steal_pct=0.0, seconds=60, t0=t)
        t += 60.0
    assert snap.over_baseline_sec > 0
    assert snap.overdraft_vcpu_min > 0

    # Drop below baseline: the streak and the integral both reset, because
    # credits start accruing again and past overdraft is no longer the story.
    for _ in range(20):
        snap = _advance(sensor, stub_host, busy_pct=5.0, steal_pct=0.0, seconds=60, t0=t)
        t += 60.0
    assert snap.over_baseline_sec == 0.0
    assert snap.overdraft_vcpu_min == 0.0


def test_disk_projection_only_when_growing(stub_host):
    sensor = HostSensor(ncpu=NCPU, baseline_pct=20.0)
    t = 0.0
    for _ in range(5):
        snap = sensor.sample(now=t)
        t += 60.0
    assert snap.disk_days_to_full is None  # flat

    for _ in range(5):
        stub_host["disk_pct"] += 1.0
        snap = sensor.sample(now=t)
        t += 60.0
    assert snap.disk_days_to_full is not None
    assert snap.disk_days_to_full > 0


def test_top_processes_stay_consistent_with_utilisation(stub_host, monkeypatch):
    """Regression: per-process shares must not exceed the box's actual usage.

    On a throttled guest the kernel's per-task utime/stime overstates work --
    measured at 3.7x the global busy delta at 64% steal, because the scheduler
    counts a task as running across slices the hypervisor stole. Dividing raw
    ticks by wall-clock capacity therefore reported single processes at 31% of
    a box that /proc/stat said was only 18% busy.
    """

    ticks = {"a": 0, "b": 0}

    def fake_procs():
        return {1: ("hog", ticks["a"]), 2: ("small", ticks["b"])}

    monkeypatch.setattr(host_sensor, "_read_process_cpu_ticks", fake_procs)
    sensor = HostSensor(ncpu=NCPU, baseline_pct=20.0)
    sensor.sample(now=0.0)

    # Inflated per-task accounting: 3000 + 1000 ticks over 20s on 2 CPUs is
    # far more than the 18% the global counter will report.
    ticks["a"] += 3000
    ticks["b"] += 1000
    snap = _advance(sensor, stub_host, busy_pct=18.0, steal_pct=64.0, seconds=20, t0=20.0)

    assert snap.cpu_util_pct == pytest.approx(18.0, abs=0.5)
    total = sum(p.cpu_pct_of_box for p in snap.top_processes)
    assert total == pytest.approx(snap.cpu_util_pct, abs=0.5)
    # Ranking is what makes the alert actionable, so it must survive rescaling.
    assert [p.name for p in snap.top_processes] == ["hog", "small"]
    assert snap.top_processes[0].cpu_pct_of_box == pytest.approx(13.5, abs=0.5)


def test_missing_proc_degrades_to_none(monkeypatch):
    """A non-Linux dev box must not raise, just report nothing."""

    monkeypatch.setattr(host_sensor, "read_cpu_times", lambda: None)
    monkeypatch.setattr(host_sensor, "_read_process_cpu_ticks", lambda: {})
    monkeypatch.setattr(host_sensor, "_read_disk", lambda path: (None, None, None))
    monkeypatch.setattr(host_sensor, "_read_swap_mb", lambda: (None, None))
    monkeypatch.setattr(host_sensor, "_read_swap_events", lambda: (None, None))
    monkeypatch.setattr(host_sensor, "_read_memory_pressure_full_avg60", lambda: None)
    snap = HostSensor(ncpu=NCPU).sample(now=0.0)
    assert snap.cpu_util_pct_avg is None
    assert snap.disk_used_pct is None


# -- evaluator ------------------------------------------------------------


def _snap(**kwargs) -> HostSnapshot:
    base = dict(created_at=0.0, ncpu=NCPU, baseline_pct=20.0)
    base.update(kwargs)
    return HostSnapshot(**base)


def test_burn_does_not_fire_before_sustain_window():
    ev = HostAlertEvaluator(HostAlertThresholds(cpu_burn_sustain_sec=1200))
    assert ev.evaluate(_snap(cpu_util_pct_avg=50.0, over_baseline_sec=600.0)) == []
    assert ev.active_codes == ()


def test_burn_fires_once_then_stays_quiet():
    ev = HostAlertEvaluator(HostAlertThresholds(cpu_burn_sustain_sec=1200))
    fired = ev.evaluate(_snap(cpu_util_pct_avg=50.0, over_baseline_sec=1260.0))
    assert [t.code for t in fired] == ["HOST_CPU_BURN"]
    assert fired[0].action == "fire"
    assert fired[0].severity == "WARN"
    # Still bad, but already reported: no repeat.
    assert ev.evaluate(_snap(cpu_util_pct_avg=55.0, over_baseline_sec=1500.0)) == []


def test_burn_resolves_when_back_under_baseline():
    ev = HostAlertEvaluator(HostAlertThresholds(cpu_burn_sustain_sec=1200))
    ev.evaluate(_snap(cpu_util_pct_avg=50.0, over_baseline_sec=1260.0))
    resolved = ev.evaluate(_snap(cpu_util_pct_avg=5.0, over_baseline_sec=0.0))
    assert [(t.action, t.code) for t in resolved] == [("resolve", "HOST_CPU_BURN")]
    assert ev.active_codes == ()


def test_throttle_alert_fires_on_sustained_steal():
    ev = HostAlertEvaluator(HostAlertThresholds(cpu_steal_alert_pct=15.0))
    assert ev.evaluate(_snap(cpu_steal_pct_avg=8.0)) == []
    fired = ev.evaluate(_snap(cpu_steal_pct_avg=60.0))
    assert [t.code for t in fired] == ["HOST_CPU_THROTTLED"]
    assert fired[0].severity == "ALERT"


def test_disk_escalation_closes_the_warn_fingerprint():
    ev = HostAlertEvaluator(HostAlertThresholds(disk_warn_pct=80.0, disk_alert_pct=90.0))
    fired = ev.evaluate(_snap(disk_used_pct=85.0))
    assert [(t.action, t.fingerprint) for t in fired] == [("fire", "HOST_DISK:warn")]

    escalated = ev.evaluate(_snap(disk_used_pct=93.0))
    # The warn fingerprint must be closed, otherwise its recovered notice
    # would never arrive and the dedupe record would leak.
    assert [(t.action, t.fingerprint) for t in escalated] == [
        ("resolve", "HOST_DISK:warn"),
        ("fire", "HOST_DISK:alert"),
    ]
    assert escalated[1].severity == "ALERT"


def _mem_ev() -> HostAlertEvaluator:
    return HostAlertEvaluator(
        HostAlertThresholds(mem_pressure_full_avg60=5.0, swap_pages_per_sec_warn=200.0)
    )


def test_mem_pressure_fires_on_either_psi_or_paging_rate():
    ev = _mem_ev()
    assert ev.evaluate(_snap(mem_pressure_full_avg60=1.0, swap_pages_per_sec=5.0)) == []
    assert [
        t.code for t in ev.evaluate(_snap(mem_pressure_full_avg60=9.0, swap_pages_per_sec=5.0))
    ] == ["HOST_MEM_PRESSURE"]

    ev2 = _mem_ev()
    assert [
        t.code for t in ev2.evaluate(_snap(mem_pressure_full_avg60=0.1, swap_pages_per_sec=900.0))
    ] == ["HOST_MEM_PRESSURE"]


def test_parked_swap_with_flat_psi_is_not_pressure():
    """Regression: 611MB of cold swap left over from an incident is not an alert.

    Observed on the live host -- swap_used sat at 611MB for hours with PSI
    pinned at 0.0 and no paging traffic. An occupancy threshold fired on every
    single sample; the rate-based rule must stay silent.
    """

    ev = _mem_ev()
    snap = _snap(
        mem_pressure_full_avg60=0.0,
        swap_used_mb=611.0,
        swap_total_mb=2048.0,
        swap_pages_per_sec=0.0,
    )
    assert ev.evaluate(snap) == []
    assert ev.active_codes == ()


def test_incident_20260817_replay(stub_host):
    """Replay the real incident and assert the lead time.

    Measured timeline (UTC), from sar on the affected host:
        01:10  utilisation 14% -> ~50%, credits begin draining
        03:10  steal 3.3% -> 10%
        03:20  steal -> 66%, service effectively dead

    A steal-only rule fires at 03:20 with nothing left to do but shed load.
    The sustained-burn rule must fire around 01:30.
    """

    sensor = HostSensor(ncpu=NCPU, baseline_pct=20.0)
    ev = HostAlertEvaluator(
        HostAlertThresholds(cpu_burn_sustain_sec=1200, cpu_steal_alert_pct=15.0)
    )

    minute = 0
    t = 0.0
    burn_fired_at: int | None = None
    throttle_fired_at: int | None = None

    sensor.sample(now=t)
    # 01:10 -> 03:10 : two hours above baseline, steal still low.
    # 03:10 -> 03:30 : steal ramps as the balance runs out.
    while minute < 140:
        minute += 1
        t += 60.0
        steal = 3.0 if minute < 120 else 66.0
        busy = 50.0 if minute < 120 else 11.0
        snap = _advance(sensor, stub_host, busy_pct=busy, steal_pct=steal, seconds=60, t0=t)
        for transition in ev.evaluate(snap):
            if transition.action != "fire":
                continue
            if transition.code == "HOST_CPU_BURN" and burn_fired_at is None:
                burn_fired_at = minute
            if transition.code == "HOST_CPU_THROTTLED" and throttle_fired_at is None:
                throttle_fired_at = minute

    assert burn_fired_at is not None, "sustained-burn alert never fired"
    assert throttle_fired_at is not None, "throttle alert never fired"
    # Burn fires once the 20-minute sustain window closes, allowing for the
    # rolling mean to climb out of its priming samples.
    assert 20 <= burn_fired_at <= 35
    lead_minutes = throttle_fired_at - burn_fired_at
    assert lead_minutes >= 90, f"expected >=90min of lead time, got {lead_minutes}"
