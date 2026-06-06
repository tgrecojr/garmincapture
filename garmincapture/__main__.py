"""Entrypoint: ``python -m garmincapture``.

Modes:
  Incremental (default): an always-on loop — pull a trailing ``LOOKBACK_DAYS``
                         window, sleep ``POLL_INTERVAL_SECONDS``, repeat.
  ``--once``:            a single incremental pull, then exit (cron / testing).
  Backfill:              ``--start YYYY-MM-DD --end YYYY-MM-DD`` — one-off,
                         reverse-chronological; never loops.

First run is interactive for MFA; the persisted token store carries subsequent
unattended runs. SIGTERM/SIGINT (e.g. ``docker stop``) shut the loop down
cleanly between cycles.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from datetime import date

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from . import auth
from .config import load_settings
from .logging_config import configure_logging, get_logger
from .runner import Runner, build_window

log = get_logger(__name__)

# Set by SIGTERM/SIGINT so the poll loop stops cleanly between cycles.
_stop = threading.Event()


def _handle_signal(signum, _frame) -> None:
    log.info("shutdown_signal", signal=signum)
    _stop.set()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="garmincapture",
        description="Thin Garmin Connect raw-capture service feeding the bronze layer.",
    )
    parser.add_argument("--start", type=_parse_date, default=None, help="Backfill start date (YYYY-MM-DD).")
    parser.add_argument("--end", type=_parse_date, default=None, help="Backfill end date (YYYY-MM-DD).")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single incremental pull and exit (no poll loop).",
    )
    return parser.parse_args(argv)


def _pull_once(settings, client, **window_kwargs) -> None:
    window = build_window(settings, **window_kwargs)
    Runner(settings, client).run(window)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    settings = load_settings()

    if not settings.capture_enabled:
        log.warning("bronze_disabled", reason="BRONZE_ROOT unset/empty; capture is a noop")

    try:
        client = auth.login(settings)
    except Exception as exc:  # noqa: BLE001 — surface auth failures clearly
        log.error("login_failed", error=str(exc))
        return 1

    # Backfill: explicit, one-off, never loops.
    if args.start is not None or args.end is not None:
        _pull_once(settings, client, start=args.start, end=args.end)
        return 0

    # Incremental: single run (--once) or the always-on poll loop.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    interval = settings.poll_interval_seconds

    while not _stop.is_set():
        try:
            _pull_once(settings, client)
        except GarminConnectTooManyRequestsError as exc:
            log.warning("rate_limited", error=str(exc), action="backing off until next cycle")
        except GarminConnectAuthenticationError as exc:
            log.warning("reauth_required", error=str(exc))
            try:
                client = auth.login(settings)
            except Exception as exc2:  # noqa: BLE001
                log.error("relogin_failed", error=str(exc2))
                return 1

        if args.once:
            break

        log.info("sleeping", seconds=interval)
        _stop.wait(interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
