import os
import time
from datetime import datetime, timezone, timedelta
from typing import Iterable, Optional

from dagster import DagsterInstance, DagsterRunStatus, RunsFilter
from opentelemetry import metrics
from opentelemetry.metrics import Observation

from luban_dagster_platform.otel import configure_otel

from dagster._core.definitions.run_request import InstigatorType
from dagster._core.scheduler.instigation import InstigatorStatus


# ---------------------------------------------------------------------------
# Status encoding for OTel gauge values
#   1  = SUCCESS
#   0  = FAILURE
#  -1  = STARTED / STARTING / NOT_STARTED (still executing)
#  -2  = QUEUED
#  -3  = CANCELED / CANCELING
# ---------------------------------------------------------------------------
_STATUS_VALUE: dict[str, int] = {
    DagsterRunStatus.SUCCESS.value:   1,   # type: ignore[attr-defined]
    DagsterRunStatus.FAILURE.value:   0,   # type: ignore[attr-defined]
    DagsterRunStatus.STARTED.value:  -1,   # type: ignore[attr-defined]
    DagsterRunStatus.STARTING.value: -1,   # type: ignore[attr-defined]
    DagsterRunStatus.NOT_STARTED.value: -1, # type: ignore[attr-defined]
    DagsterRunStatus.QUEUED.value:   -2,   # type: ignore[attr-defined]
    DagsterRunStatus.CANCELED.value: -3,   # type: ignore[attr-defined]
    DagsterRunStatus.CANCELING.value: -3,  # type: ignore[attr-defined]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enabled(value: Optional[str]) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized not in {"", "none", "false", "0"}


def _run_count(instance: DagsterInstance, statuses: Iterable[DagsterRunStatus]) -> int:
    return instance.get_runs_count(RunsFilter(statuses=list(statuses)))


def _oldest_run_age_seconds(instance: DagsterInstance, statuses: Iterable[DagsterRunStatus]) -> float:
    records = instance.get_run_records(
        RunsFilter(statuses=list(statuses)),
        limit=1,
        order_by="create_timestamp",
        ascending=True,
    )
    if not records:
        return 0.0

    now = time.time()
    ts = records[0].create_timestamp
    if ts is not None:
        if ts.tzinfo is not None:
            ts_epoch = ts.timestamp()
        else:
            import calendar
            ts_epoch = calendar.timegm(ts.timetuple())
        return max(0.0, now - ts_epoch)
    return 0.0


def _daemon_heartbeat_ages_seconds(instance: DagsterInstance) -> dict[str, float]:
    now = time.time()
    ages_by_type: dict[str, float] = {}

    for heartbeat in instance.get_daemon_heartbeats().values():
        daemon_type = heartbeat.daemon_type
        age = max(0.0, now - heartbeat.timestamp)
        if daemon_type not in ages_by_type or age < ages_by_type[daemon_type]:
            ages_by_type[daemon_type] = age

    return ages_by_type


def _daemon_heartbeat_error_counts(instance: DagsterInstance) -> dict[str, int]:
    counts_by_type: dict[str, int] = {}
    for heartbeat in instance.get_daemon_heartbeats().values():
        daemon_type = heartbeat.daemon_type
        counts_by_type[daemon_type] = counts_by_type.get(daemon_type, 0) + len(heartbeat.errors or [])
    return counts_by_type


def _instigator_selector_id(state) -> str:
    repo_origin = getattr(state.origin, "repository_origin", None)
    if repo_origin is not None and hasattr(repo_origin, "get_selector_id"):
        return repo_origin.get_selector_id()
    return state.origin.get_id()


def _instigator_last_tick_age_seconds(instance: DagsterInstance, state) -> Optional[float]:
    origin_id = state.origin.get_id()
    selector_id = _instigator_selector_id(state)

    try:
        ticks = instance.get_ticks(origin_id=origin_id, selector_id=selector_id, limit=1)
    except Exception:
        ticks = instance.get_ticks(origin_id=origin_id, selector_id=origin_id, limit=1)

    if not ticks:
        return None

    now = time.time()
    return max(0.0, now - ticks[0].timestamp)


# ---------------------------------------------------------------------------
# Job-level run status helpers
# ---------------------------------------------------------------------------

def _job_last_run_statuses(instance: DagsterInstance, limit: int = 500) -> dict[str, tuple[int, float]]:
    """Return {pipeline_name: (status_value, age_seconds)} for the latest run of each pipeline."""
    records = instance.get_run_records(
        RunsFilter(),
        limit=limit,
        order_by="create_timestamp",
        ascending=False,
    )
    now = time.time()
    result: dict[str, tuple[int, float]] = {}
    for record in records:
        run = record.dagster_run
        pname = run.job_name or run.pipeline_name or "unknown"
        if pname in result:
            continue  # first seen = latest run for this pipeline
        status_val = _STATUS_VALUE.get(run.status.value, -99)  # type: ignore[attr-defined]
        ts = record.create_timestamp
        age = 0.0
        if ts is not None:
            if ts.tzinfo is not None:
                ts_epoch = ts.timestamp()
            else:
                import calendar
                ts_epoch = calendar.timegm(ts.timetuple())
            age = max(0.0, now - ts_epoch)
        result[pname] = (status_val, age)
    return result


def _job_concurrency(instance: DagsterInstance) -> dict[str, int]:
    """Return {pipeline_name: count_of_currently_running_runs}."""
    records = instance.get_run_records(
        RunsFilter(statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.STARTING, DagsterRunStatus.NOT_STARTED]),
        limit=200,
        order_by="create_timestamp",
        ascending=False,
    )
    counts: dict[str, int] = {}
    for record in records:
        run = record.dagster_run
        pname = run.job_name or run.pipeline_name or "unknown"
        counts[pname] = counts.get(pname, 0) + 1
    return counts


def _job_counts_15m(instance: DagsterInstance) -> dict[str, tuple[int, int, int]]:
    """Return {pipeline_name: (success_count, failure_count, queued_count)} in last 15 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    records = instance.get_run_records(
        RunsFilter(created_after=cutoff),
        limit=1000,
        order_by="create_timestamp",
        ascending=False,
    )
    success: dict[str, int] = {}
    failure: dict[str, int] = {}
    queued: dict[str, int] = {}
    for record in records:
        run = record.dagster_run
        pname = run.job_name or run.pipeline_name or "unknown"
        status = run.status.value  # type: ignore[attr-defined]
        if status == DagsterRunStatus.SUCCESS.value:
            success[pname] = success.get(pname, 0) + 1
        elif status == DagsterRunStatus.FAILURE.value:
            failure[pname] = failure.get(pname, 0) + 1
        elif status == DagsterRunStatus.QUEUED.value:
            queued[pname] = queued.get(pname, 0) + 1
    all_keys = set(success) | set(failure) | set(queued)
    return {k: (success.get(k, 0), failure.get(k, 0), queued.get(k, 0)) for k in all_keys}


# ===================================================================
# main
# ===================================================================

def main() -> None:
    export_interval_millis = int(os.getenv("LUBAN_OTEL_METRICS_EXPORT_INTERVAL_MILLIS") or "60000")
    if not _enabled(os.getenv("OTEL_METRICS_EXPORTER")):
        while True:
            time.sleep(3600)

    configure_otel(export_interval_millis=export_interval_millis)

    instance = DagsterInstance.get()
    meter = metrics.get_meter("luban.dagster.platform")

    # ---- Queue metrics ----

    def queued_cb(_options):
        yield Observation(_run_count(instance, [DagsterRunStatus.QUEUED]))

    def queued_oldest_age_cb(_options):
        yield Observation(_oldest_run_age_seconds(instance, [DagsterRunStatus.QUEUED]))

    def in_progress_cb(_options):
        yield Observation(
            _run_count(instance, [DagsterRunStatus.NOT_STARTED, DagsterRunStatus.STARTING, DagsterRunStatus.STARTED])
        )

    # ---- Sensor / schedule metrics ----

    def sensors_enabled_cb(_options):
        states = instance.all_instigator_state(instigator_type=InstigatorType.SENSOR)
        yield Observation(sum(1 for s in states if s.status == InstigatorStatus.RUNNING))

    def schedules_enabled_cb(_options):
        states = instance.all_instigator_state(instigator_type=InstigatorType.SCHEDULE)
        yield Observation(sum(1 for s in states if s.status == InstigatorStatus.RUNNING))

    def sensor_last_tick_age_cb(_options):
        states = instance.all_instigator_state(instigator_type=InstigatorType.SENSOR)
        for s in states:
            age = _instigator_last_tick_age_seconds(instance, s)
            if age is None:
                continue
            yield Observation(age, attributes={"dagster.instigator_name": s.origin.instigator_name, "dagster.instigator_status": s.status.value})

    def schedule_last_tick_age_cb(_options):
        states = instance.all_instigator_state(instigator_type=InstigatorType.SCHEDULE)
        for s in states:
            age = _instigator_last_tick_age_seconds(instance, s)
            if age is None:
                continue
            yield Observation(age, attributes={"dagster.instigator_name": s.origin.instigator_name, "dagster.instigator_status": s.status.value})

    # ---- Daemon metrics ----

    def daemon_heartbeats_count_cb(_options):
        yield Observation(len(instance.get_daemon_heartbeats()))

    def daemon_heartbeat_age_cb(_options):
        for daemon_type, age in _daemon_heartbeat_ages_seconds(instance).items():
            yield Observation(age, attributes={"dagster.daemon_type": daemon_type})

    def daemon_heartbeat_errors_cb(_options):
        for daemon_type, count in _daemon_heartbeat_error_counts(instance).items():
            yield Observation(count, attributes={"dagster.daemon_type": daemon_type})

    # ================================================================
    # NEW — Job-level (code location) run status metrics
    # ================================================================

    def job_last_status_cb(_options):
        statuses = _job_last_run_statuses(instance)
        for pname, (sval, _age) in statuses.items():
            yield Observation(sval, attributes={"dagster.pipeline_name": pname})

    def job_last_run_ago_cb(_options):
        statuses = _job_last_run_statuses(instance)
        for pname, (_sval, age) in statuses.items():
            yield Observation(age, attributes={"dagster.pipeline_name": pname})

    def job_concurrency_cb(_options):
        running = _job_concurrency(instance)
        for pname, count in running.items():
            yield Observation(count, attributes={"dagster.pipeline_name": pname})

    def job_success_count_15m_cb(_options):
        counts = _job_counts_15m(instance)
        total = 0
        for pname, (sc, _fc, _qc) in counts.items():
            yield Observation(sc, attributes={"dagster.pipeline_name": pname})
            total += sc
        yield Observation(total, attributes={"dagster.pipeline_name": "__total__"})

    def job_failure_count_15m_cb(_options):
        counts = _job_counts_15m(instance)
        total = 0
        for pname, (_sc, fc, _qc) in counts.items():
            yield Observation(fc, attributes={"dagster.pipeline_name": pname})
            total += fc
        yield Observation(total, attributes={"dagster.pipeline_name": "__total__"})

    def job_queued_count_15m_cb(_options):
        counts = _job_counts_15m(instance)
        total = 0
        for pname, (_sc, _fc, qc) in counts.items():
            yield Observation(qc, attributes={"dagster.pipeline_name": pname})
            total += qc
        yield Observation(total, attributes={"dagster.pipeline_name": "__total__"})

    # ---- Register all gauges ----

    meter.create_observable_gauge("dagster.run.queue.depth", callbacks=[queued_cb], unit="1")
    meter.create_observable_gauge("dagster.run.queue.oldest_age_seconds", callbacks=[queued_oldest_age_cb], unit="s")
    meter.create_observable_gauge("dagster.run.in_progress.count", callbacks=[in_progress_cb], unit="1")

    meter.create_observable_gauge("dagster.sensor.enabled.count", callbacks=[sensors_enabled_cb], unit="1")
    meter.create_observable_gauge("dagster.schedule.enabled.count", callbacks=[schedules_enabled_cb], unit="1")
    meter.create_observable_gauge("dagster.sensor.last_tick_age_seconds", callbacks=[sensor_last_tick_age_cb], unit="s")
    meter.create_observable_gauge("dagster.schedule.last_tick_age_seconds", callbacks=[schedule_last_tick_age_cb], unit="s")

    meter.create_observable_gauge("dagster.daemon.heartbeat.count", callbacks=[daemon_heartbeats_count_cb], unit="1")
    meter.create_observable_gauge("dagster.daemon.heartbeat_age_seconds", callbacks=[daemon_heartbeat_age_cb], unit="s")
    meter.create_observable_gauge("dagster.daemon.heartbeat_errors.count", callbacks=[daemon_heartbeat_errors_cb], unit="1")

    # ---- New job-level gauges ----
    # dagster.job.run.last_status         — last run status per pipeline (1=success, 0=failure, -1=running, -2=queued, -3=canceled)
    # dagster.job.run.last_run_ago_seconds — seconds since last run per pipeline
    # dagster.job.run.concurrency         — currently executing runs per pipeline
    # dagster.job.run.success_count_15m   — successful runs in last 15min per pipeline
    # dagster.job.run.failure_count_15m   — failed runs in last 15min per pipeline
    # dagster.job.run.queued_count_15m    — queued runs in last 15min per pipeline

    meter.create_observable_gauge("dagster.job.run.last_status", callbacks=[job_last_status_cb], unit="1")
    meter.create_observable_gauge("dagster.job.run.last_run_ago_seconds", callbacks=[job_last_run_ago_cb], unit="s")
    meter.create_observable_gauge("dagster.job.run.concurrency", callbacks=[job_concurrency_cb], unit="1")
    meter.create_observable_gauge("dagster.job.run.success_count_15m", callbacks=[job_success_count_15m_cb], unit="1")
    meter.create_observable_gauge("dagster.job.run.failure_count_15m", callbacks=[job_failure_count_15m_cb], unit="1")
    meter.create_observable_gauge("dagster.job.run.queued_count_15m", callbacks=[job_queued_count_15m_cb], unit="1")

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
