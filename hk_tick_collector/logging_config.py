import logging

# The Futu Python SDK keeps its own loggers with `propagate = False` and
# hardcodes the file handler to DEBUG, so LOG_LEVEL never reaches it. It emits
# two lines per tick push; across the HK universe that measured ~55MB/hour
# (~1.3GB/day) on 2026-08-17 -- more bytes written to the debug log than to the
# tick database itself. The SDK's documented switch (FTLog.debug_model) is
# attached to the wrong property setter in some releases, so drive stdlib
# logging directly, which is version-independent.
_FUTU_SDK_LOGGERS = ("FTFileLog", "FTConsoleLog")


def quiet_futu_sdk_logs(level: str = "WARNING") -> None:
    """Raise the Futu SDK's own log level.

    Must run *after* ``futu`` has been imported: the SDK builds its FTLog
    singleton at import time and that constructor sets the level to DEBUG,
    which would silently undo an earlier call. Importing
    ``hk_tick_collector.futu_client`` is what pulls the SDK in.

    Set ``FUTU_SDK_LOG_LEVEL=DEBUG`` to restore the firehose when actually
    debugging the SDK's transport.
    """

    resolved = logging.getLevelName(str(level).upper())
    if not isinstance(resolved, int):
        resolved = logging.WARNING
    for name in _FUTU_SDK_LOGGERS:
        sdk_logger = logging.getLogger(name)
        sdk_logger.setLevel(resolved)
        for handler in sdk_logger.handlers:
            handler.setLevel(resolved)


def setup_logging(level: str, futu_sdk_level: str = "WARNING") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    quiet_futu_sdk_logs(futu_sdk_level)
