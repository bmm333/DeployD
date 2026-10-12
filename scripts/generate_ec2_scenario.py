"""Generate data/scenarios/live_ec2_os_upgrade.json: a production-like day of events.

The story (a real class of incident, not a specific vendor bug): ten identical EC2 web
instances behind a load balancer. During a night maintenance window AWS Systems Manager
upgrades the OS packages of four of them (services are not restarted, so nothing breaks).
In the morning a normal release goes out through git and CI/CD, all green, and restarts the
processes: the four upgraded instances now load the new system libraries and answer 500,
the other six do not. The shallow health check keeps passing, so the load balancer keeps
routing to the broken instances. The team suspects the release and rolls it back; the
errors stay, because the release was never the difference between the two groups.

The log is generated, not hand-typed, so that its size and noise are reproducible: a fixed
seed drives every jitter. Nothing in it names a package: the cause is a change that exists
on the failing instances and on none of the healthy ones, which is what the engine finds.

    python scripts/generate_ec2_scenario.py
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OUT = Path("data/scenarios/live_ec2_os_upgrade.json")
DAY = datetime(2026, 10, 8, tzinfo=timezone.utc)
HOSTS = [f"web-{i:02d}" for i in range(1, 11)]
BAD = ["web-03", "web-05", "web-07", "web-09"]
WAVES = [
    ("web-01", "web-02"),
    ("web-03", "web-04"),
    ("web-05", "web-06"),
    ("web-07", "web-08"),
    ("web-09", "web-10"),
]
PEER_GROUP = "web"  # what an auto scaling group name would be
RELEASE, PREVIOUS = "2.14.0", "2.13.9"
PATCH_RUN = "patch-baseline-2026-10-07"

rng = random.Random(20261008)
events: list[dict[str, Any]] = []


def at(hours: int, minutes: int = 0, seconds: int = 0) -> datetime:
    return DAY + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def emit(when: datetime, source: str, event_type: str, description: str, **metadata: Any) -> None:
    if source in HOSTS:  # the collector tags every event with the group the instance belongs to
        metadata["peer_group"] = PEER_GROUP
    events.append(
        {
            "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source,
            "event_type": event_type,
            "metadata": metadata,
            "description": description,
        }
    )


def jitter(seconds: int) -> timedelta:
    return timedelta(seconds=rng.randint(-seconds, seconds))


def heartbeat(host: str, when: datetime) -> None:
    emit(
        when,
        host,
        "HEALTHCHECK_PASS",
        f"{host} target healthy (load balancer health check /healthz answered 200 in "
        f"{rng.randint(6, 19)} ms)",
    )


def heartbeats(
    hosts: list[str], start: datetime, end: datetime, every_s: int, spread_s: int
) -> None:
    for host in hosts:
        t = start + timedelta(seconds=rng.randint(0, every_s))
        while t < end:
            heartbeat(host, t)
            t += timedelta(seconds=every_s) + jitter(spread_s)


def database(start: datetime, end: datetime, every_s: int) -> None:
    t = start
    while t < end:
        ms = rng.randint(3, 9)
        emit(
            t,
            "postgres-primary",
            "LATENCY",
            f"postgres-primary query latency p95 {ms} ms",
            latency_ms=ms,
        )
        t += timedelta(seconds=every_s) + jitter(60)


def night() -> None:
    """02:00-09:30. Systems Manager patches four instances; nothing visibly changes."""
    emit(
        at(2, 0),
        "aws-ssm",
        "MAINTENANCE_WINDOW",
        "Systems Manager maintenance window mw-weekly-patch started (4 targets)",
    )
    for host, minute in zip(BAD, (7, 11, 18, 26), strict=True):
        emit(
            at(2, minute, rng.randint(0, 50)),
            host,
            "HOST_UPGRADE",
            f"{host}: Patch Manager installed 52 package upgrades with unattended-upgrades; "
            "reboot not required, services not restarted",
            change_kind="os_upgrade",
            change_ref=PATCH_RUN,
            packages_upgraded=52,
            reboot_required=False,
            services_restarted=False,
        )
    emit(
        at(2, 41),
        "aws-ssm",
        "MAINTENANCE_WINDOW",
        "Systems Manager maintenance window mw-weekly-patch finished",
    )
    # A lone slow query during the nightly backup and a flapping target: noise, not an incident.
    emit(
        at(3, 15, 20),
        "postgres-primary",
        "LATENCY",
        "postgres-primary query latency p95 640 ms during the nightly backup",
        latency_ms=640,
    )
    emit(
        at(5, 20),
        "web-04",
        "HEALTHCHECK_FAIL",
        "web-04 target failed one load balancer health check (timeout)",
    )
    emit(
        at(5, 22),
        "web-04",
        "HEALTHCHECK_PASS",
        "web-04 target healthy again (health check /healthz answered 200 in 11 ms)",
    )
    emit(
        at(6, 10),
        "web-02",
        "HTTP_RESPONSE",
        "web-02 GET /api/cart answered 500 once (client retry succeeded)",
        status_code=500,
    )
    heartbeats(HOSTS, at(2, 0), at(9, 30), 3600, 300)
    database(at(2, 0), at(9, 30), 1800)
    for host, hour in (("web-06", 4), ("web-01", 7), ("web-08", 8)):
        emit(
            at(hour, rng.randint(0, 59)),
            host,
            "CPU_SAMPLE",
            f"{host} CPU {(pct := rng.randint(31, 66))}%",
            cpu_percent=pct,
        )


def release_pipeline() -> None:
    """09:35-10:20. Review, merge, CI: every job green; one build is much slower than usual."""
    emit(
        at(9, 41),
        "gitlab",
        "GIT_PUSH",
        "gitlab: merge request !482 merged into main (a41f9c2): 14 files changed, requirements.lock +2/-2",
        repo="shop/web",
        branch="main",
        commit="a41f9c2",
        files_changed=14,
        lockfile_changed=True,
    )
    emit(
        at(9, 42),
        "gitlab",
        "GIT_PUSH",
        f"gitlab: tag v{RELEASE} created on a41f9c2",
        repo="shop/web",
        tag=f"v{RELEASE}",
    )
    jobs = [
        ("lint", at(9, 44), 71, 70),
        ("unit-tests", at(9, 49), 243, 240),
        ("integration-tests", at(9, 59), 571, 560),
        ("build-image", at(10, 14), 861, 302),
        ("push-image", at(10, 16), 74, 70),
    ]
    for job, finished, seconds, average in jobs:
        emit(
            finished,
            "gitlab-ci",
            "PIPELINE_JOB",
            f"gitlab-ci: pipeline #48213 job {job} succeeded in {seconds // 60}m{seconds % 60:02d}s "
            f"(average {average // 60}m{average % 60:02d}s)",
            pipeline_id=48213,
            job=job,
            job_status="success",
            duration_ms=seconds * 1000,
            avg_duration_ms=average * 1000,
            release=RELEASE,
        )
    emit(
        at(10, 17),
        "web-staging",
        "DEPLOY_SUCCESS",
        f"web-staging: release {RELEASE} deployed",
        release=RELEASE,
    )
    for n, path in enumerate(("/healthz", "/checkout", "/api/cart"), start=1):
        emit(
            at(10, 17, 20 * n),
            "web-staging",
            "HTTP_RESPONSE",
            f"web-staging smoke test GET {path} answered 200",
            status_code=200,
        )
    emit(
        at(10, 19),
        "gitlab-ci",
        "PIPELINE_JOB",
        "gitlab-ci: pipeline #48213 job smoke-tests-staging succeeded in 0m52s (average 0m50s)",
        pipeline_id=48213,
        job="smoke-tests-staging",
        job_status="success",
        duration_ms=52_000,
        avg_duration_ms=50_000,
        release=RELEASE,
    )


def rollout(release: str, start: datetime, label: str) -> dict[str, datetime]:
    """Rolling deploy in waves of two instances; returns when each instance finished."""
    finished: dict[str, datetime] = {}
    t = start
    for wave in WAVES:
        for host in wave:
            emit(
                t + jitter(3),
                host,
                "DEPLOY_START",
                f"{host}: {label} {release} started (drain, stop, start)",
                change_kind="deploy",
                change_ref=release,
                release=release,
            )
            emit(
                t + timedelta(seconds=22),
                host,
                "PROCESS_START",
                f"{host}: application service restarted",
            )
            done = t + timedelta(seconds=rng.randint(55, 80))
            emit(
                done,
                host,
                "DEPLOY_SUCCESS",
                f"{host}: {label} {release} finished, target registered",
                change_kind="deploy",
                change_ref=release,
                release=release,
            )
            heartbeat(host, done + timedelta(seconds=rng.randint(15, 30)))
            finished[host] = done
        t += timedelta(seconds=rng.randint(420, 500))
    return finished


def failures(deployed: dict[str, datetime], until: datetime) -> None:
    """The four upgraded instances answer 500 after their restart; the others do not."""
    for host in BAD:
        t = deployed[host] + timedelta(seconds=rng.randint(35, 55))
        emit(
            t,
            host,
            "HTTP_ERROR",
            f"{host}: request failed with 500: symbol lookup error in a native extension "
            "while opening a database connection",
            dependency="postgres-primary",
        )
        for n, path in enumerate(("/checkout", "/api/cart", "/orders"), start=1):
            emit(
                t + timedelta(seconds=3 * n),
                host,
                "HTTP_RESPONSE",
                f"{host} POST {path} answered 500",
                status_code=500,
            )
        t += timedelta(minutes=3)
        while t < until:
            busy = t < at(13, 30)  # fine-grained while someone is watching, hourly-ish after
            share = rng.randint(14, 19)
            total = rng.randint(1100, 1400) if busy else rng.randint(700, 900)
            minutes = 3 if busy else 20
            emit(
                t,
                host,
                "HTTP_RESPONSE",
                f"{host}: {share}% of requests answered 500 in the last {minutes} minutes "
                f"({total * share // 100}/{total})",
                status_code=500,
                error_rate_pct=share,
            )
            t += timedelta(seconds=rng.randint(165, 215) if busy else rng.randint(1100, 1300))


def load_balancer(start: datetime, end: datetime) -> None:
    t = start
    while t < end:
        share = rng.randint(37, 44) / 10
        emit(
            t,
            "web-alb",
            "HTTP_RESPONSE",
            f"web-alb: target 5xx {share}% of requests, concentrated on 4 of 10 targets",
            status_code=502,
            error_rate_pct=share,
        )
        t += timedelta(seconds=rng.randint(270, 330))


def incident_day() -> None:
    """10:20 onward: rollout, errors, the alert, a rollback that fixes nothing."""
    deployed = rollout(RELEASE, at(10, 22), "deploy")
    emit(
        at(10, 52),
        "gitlab-ci",
        "PIPELINE_JOB",
        "gitlab-ci: pipeline #48213 job deploy-prod succeeded in 11m40s (average 11m10s)",
        pipeline_id=48213,
        job="deploy-prod",
        job_status="success",
        duration_ms=700_000,
        avg_duration_ms=670_000,
        release=RELEASE,
    )
    failures(deployed, at(18, 40))
    # Load balancer health checks are shallow: every instance keeps passing them.
    heartbeats(HOSTS, at(10, 23), at(11, 40), 240, 40)
    heartbeats(HOSTS, at(11, 40), at(13, 30), 600, 60)
    heartbeats(HOSTS, at(13, 30), at(18, 40), 3600, 300)
    load_balancer(at(10, 53), at(13, 30))
    database(at(9, 30), at(18, 40), 600)
    # The rollback is the obvious reaction; the same four instances keep failing.
    emit(
        at(11, 38),
        "gitlab-ci",
        "PIPELINE_JOB",
        f"gitlab-ci: pipeline #48230 job rollback-prod started for {PREVIOUS}",
        pipeline_id=48230,
        job="rollback-prod",
        job_status="running",
        release=PREVIOUS,
    )
    rollout(PREVIOUS, at(11, 40), "rollback")
    emit(
        at(12, 12),
        "gitlab-ci",
        "PIPELINE_JOB",
        "gitlab-ci: pipeline #48230 job rollback-prod succeeded in 13m02s (average 11m10s)",
        pipeline_id=48230,
        job="rollback-prod",
        job_status="success",
        duration_ms=782_000,
        avg_duration_ms=670_000,
        release=PREVIOUS,
    )


def main() -> None:
    night()
    release_pipeline()
    incident_day()
    events.sort(key=lambda e: e["timestamp"])
    OUT.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    print(f"{len(events)} events, {events[0]['timestamp']} -> {events[-1]['timestamp']} -> {OUT}")


if __name__ == "__main__":
    main()
