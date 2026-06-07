"""Tests for window building and the orchestration runner (mocked client)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from garmincapture.runner import Runner, Window, build_window


def _payloads(root: Path, pattern: str) -> list[Path]:
    """Glob matches, excluding ``.meta.json`` sidecars."""
    return [p for p in root.glob(pattern) if not p.name.endswith(".meta.json")]


class TestWindow:
    def test_days_reverse_chronological(self):
        w = Window(start=date(2026, 6, 4), end=date(2026, 6, 6))
        assert w.days() == ["2026-06-06", "2026-06-05", "2026-06-04"]

    def test_range(self):
        w = Window(start=date(2026, 6, 4), end=date(2026, 6, 6))
        assert w.range() == ("2026-06-04", "2026-06-06")

    def test_incremental_window_uses_lookback(self, fixed_today):
        from garmincapture.config import Settings

        s = Settings(LOOKBACK_DAYS=3)
        w = build_window(s, today=fixed_today)
        # 3-day trailing window inclusive of today.
        assert w.days() == ["2026-06-06", "2026-06-05", "2026-06-04"]

    def test_backfill_window_explicit(self, fixed_today):
        from garmincapture.config import Settings

        w = build_window(Settings(), today=fixed_today, start=date(2026, 1, 1), end=date(2026, 1, 3))
        assert w.range() == ("2026-01-01", "2026-01-03")

    def test_reversed_backfill_range_rejected(self, fixed_today):
        import pytest
        from garmincapture.config import Settings

        with pytest.raises(ValueError, match="after end"):
            build_window(Settings(), today=fixed_today, start=date(2026, 1, 10), end=date(2026, 1, 1))


class TestRunnerEndToEnd:
    def _run(self, settings, client, **kw):
        window = build_window(settings, today=date(2026, 6, 6), **kw)
        runner = Runner(settings, client)
        stats = runner.run(window)
        return runner, stats

    def test_disabled_bronze_writes_no_files(self, disabled_settings, mock_client, tmp_path):
        runner, stats = self._run(disabled_settings, mock_client)
        # Library still called, but nothing landed.
        assert mock_client.get_sleep_data.called
        assert stats["captured"] == 0
        assert list(tmp_path.rglob("*.json")) == []

    def test_files_land_at_expected_paths(self, bronze_settings, mock_client):
        runner, stats = self._run(bronze_settings, mock_client)
        root = Path(bronze_settings.bronze_root)
        # Daily metric present, partitioned by UTC fetch date.
        sleep_files = _payloads(root, "garmin/sleep/dt=*/sleep_*.json")
        assert sleep_files, "expected at least one sleep capture"
        # Sidecar parses and references the right collection.
        sidecar = json.loads(Path(str(sleep_files[0]) + ".meta.json").read_text())
        assert sidecar["collection"] == "sleep"
        assert sidecar["capture_mode"] == "reserialized"
        assert stats["captured"] > 0

    def test_fit_captured_as_raw_zip(self, bronze_settings, mock_client):
        self._run(bronze_settings, mock_client)
        root = Path(bronze_settings.bronze_root)
        fit = _payloads(root, "garmin/activity_fit/dt=*/activity_fit_*.zip")
        assert fit, "expected a raw FIT/original zip capture"
        sidecar = json.loads(Path(str(fit[0]) + ".meta.json").read_text())
        assert sidecar["capture_mode"] == "raw"
        assert sidecar["charset"] is None
        assert Path(fit[0]).read_bytes() == b"PK\x03\x04fake-zip-bytes"

    def test_user_profile_secret_redacted(self, bronze_settings, mock_client):
        self._run(bronze_settings, mock_client)
        root = Path(bronze_settings.bronze_root)
        files = _payloads(root, "garmin/user_settings/dt=*/user_settings_*.json")
        assert files
        payload = json.loads(Path(files[0]).read_text())
        assert "accessToken" not in payload  # screened out
        sidecar = json.loads(Path(str(files[0]) + ".meta.json").read_text())
        assert "accessToken" in sidecar["redacted_fields"]

    def test_hrv_none_is_skipped(self, bronze_settings, mock_client):
        self._run(bronze_settings, mock_client)
        root = Path(bronze_settings.bronze_root)
        assert list(root.glob("garmin/hrv/**/*.json")) == []

    def test_no_bucket_d_methods_invoked(self, bronze_settings, mock_client):
        self._run(bronze_settings, mock_client)
        forbidden = [
            "add_weigh_in", "set_activity_name", "create_manual_activity",
            "delete_activity", "upload_activity", "remove_gear_from_activity",
            "schedule_workout", "logout", "request_reload",
        ]
        for name in forbidden:
            assert not getattr(mock_client, name).called, f"Bucket D method called: {name}"

    def test_fetch_selection_limits_pull(self, bronze_settings, mock_client):
        from dataclasses import replace  # noqa: F401 — Settings is pydantic, rebuild instead
        from garmincapture.config import Settings

        s = Settings(
            BRONZE_ROOT=bronze_settings.bronze_root,
            RATE_LIMIT_SECONDS=0,
            FETCH_SELECTION="sleep",
        )
        self._run(s, mock_client)
        root = Path(bronze_settings.bronze_root)
        assert list(root.glob("garmin/sleep/**/*.json"))
        assert list(root.glob("garmin/floors/**/*.json")) == []

    def test_default_excludes_female_health_calls(self, bronze_settings, mock_client):
        # Default FETCH_EXCLUDE must prevent these endpoints from being *called*.
        self._run(bronze_settings, mock_client)
        assert not mock_client.get_menstrual_data_for_date.called
        assert not mock_client.get_menstrual_calendar_data.called
        assert not mock_client.get_pregnancy_summary.called
        assert mock_client.get_sleep_data.called  # control: others still run

    def test_empty_exclude_restores_female_health_calls(self, bronze_settings, mock_client):
        from garmincapture.config import Settings

        s = Settings(
            BRONZE_ROOT=bronze_settings.bronze_root,
            RATE_LIMIT_SECONDS=0,
            LOOKBACK_DAYS=2,
            FETCH_EXCLUDE="",
        )
        self._run(s, mock_client)
        assert mock_client.get_menstrual_data_for_date.called
        assert mock_client.get_pregnancy_summary.called

    def test_per_device_fanout(self, bronze_settings, mock_client):
        self._run(bronze_settings, mock_client)
        root = Path(bronze_settings.bronze_root)
        assert list(root.glob("garmin/device_settings/**/*.json"))
        mock_client.get_device_settings.assert_called()
