"""Tests for the entrypoint: backfill vs --once vs the always-on poll loop."""

from __future__ import annotations

import pytest

import garmincapture.__main__ as cli
from garminconnect import GarminConnectAuthenticationError


@pytest.fixture(autouse=True)
def _reset_stop():
    """The stop Event is module-global; clear it around each test."""
    cli._stop.clear()
    yield
    cli._stop.clear()


class _RecordingRunner:
    """Stand-in for Runner that records each run() and never touches the network."""

    calls: list = []

    def __init__(self, settings, client):
        self.settings = settings
        self.client = client

    def run(self, window):
        type(self).calls.append(window)
        return {"captured": 0, "skipped": 0, "failed": 0}


@pytest.fixture
def patched_cli(monkeypatch):
    _RecordingRunner.calls = []
    monkeypatch.setattr(cli.auth, "login", lambda settings: object())
    monkeypatch.setattr(cli, "Runner", _RecordingRunner)
    return _RecordingRunner


class TestEntrypoint:
    def test_login_failure_returns_1(self, monkeypatch):
        def boom(settings):
            raise RuntimeError("auth down")

        monkeypatch.setattr(cli.auth, "login", boom)
        assert cli.main([]) == 1

    def test_once_runs_a_single_pull(self, patched_cli):
        rc = cli.main(["--once"])
        assert rc == 0
        assert len(patched_cli.calls) == 1

    def test_backfill_runs_once_no_loop(self, patched_cli):
        rc = cli.main(["--start", "2025-01-01", "--end", "2025-01-03"])
        assert rc == 0
        assert len(patched_cli.calls) == 1
        # Backfill window honors the explicit range.
        w = patched_cli.calls[0]
        assert w.range() == ("2025-01-01", "2025-01-03")

    def test_loop_exits_when_stop_set(self, monkeypatch):
        """The poll loop runs a cycle, then exits cleanly once _stop is set."""
        _RecordingRunner.calls = []

        class _StopAfterFirst(_RecordingRunner):
            def run(self, window):
                result = super().run(window)
                cli._stop.set()  # simulate SIGTERM arriving during the cycle
                return result

        monkeypatch.setattr(cli.auth, "login", lambda settings: object())
        monkeypatch.setattr(cli, "Runner", _StopAfterFirst)
        # No --once: relies on _stop to terminate the loop.
        rc = cli.main([])
        assert rc == 0
        assert len(_StopAfterFirst.calls) == 1

    def test_reauth_on_auth_error_then_stop(self, monkeypatch):
        """An auth error mid-loop triggers a re-login, not a crash."""
        _RecordingRunner.calls = []
        logins = {"count": 0}

        def fake_login(settings):
            logins["count"] += 1
            return object()

        class _AuthThenStop(_RecordingRunner):
            def run(self, window):
                cli._stop.set()
                raise GarminConnectAuthenticationError("token rejected")

        monkeypatch.setattr(cli.auth, "login", fake_login)
        monkeypatch.setattr(cli, "Runner", _AuthThenStop)
        rc = cli.main([])
        assert rc == 0
        # Initial login + one re-login after the auth error.
        assert logins["count"] == 2
