from datetime import timedelta

from deployd.domain.health.process_health import ProcessHealthFSM

_DEFAULT_RECOVERY_WINDOW = timedelta(minutes=5)
_DEFAULT_MAX_RESTART_COUNT = 3
_DEFAULT_RESTART_TIME_WINDOW = timedelta(minutes=10)


def build_process_health_fsm(
    recovery_window: timedelta = _DEFAULT_RECOVERY_WINDOW,
    max_restart_count: int = _DEFAULT_MAX_RESTART_COUNT,
    restart_time_window: timedelta = _DEFAULT_RESTART_TIME_WINDOW,
) -> ProcessHealthFSM:
    return ProcessHealthFSM(
        recovery_window=recovery_window,
        max_restart_count=max_restart_count,
        restart_time_window=restart_time_window,
    )


# factory
