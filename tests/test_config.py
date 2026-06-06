"""Tests for environment-driven configuration."""

from __future__ import annotations

from garmincapture.config import Settings


class TestCaptureEnabled:
    def test_disabled_when_unset(self):
        assert Settings(BRONZE_ROOT="").capture_enabled is False

    def test_disabled_when_whitespace(self):
        assert Settings(BRONZE_ROOT="   ").capture_enabled is False

    def test_enabled_when_set(self):
        assert Settings(BRONZE_ROOT="/data/bronze").capture_enabled is True


class TestFetchSelection:
    def test_empty_selection_selects_everything(self):
        s = Settings(FETCH_SELECTION="")
        assert s.is_selected("sleep") is True
        assert s.is_selected("anything") is True

    def test_subset_selection(self):
        s = Settings(FETCH_SELECTION="sleep, activities ,hrv")
        assert s.fetch_selection_set == {"sleep", "activities", "hrv"}
        assert s.is_selected("sleep") is True
        assert s.is_selected("floors") is False


class TestDefaults:
    def test_sensible_defaults(self):
        s = Settings()
        assert s.lookback_days == 7
        assert s.weekly_weeks == 4
        assert s.rate_limit_seconds == 2.0
        assert s.poll_interval_seconds == 900  # 15 minutes
        assert s.garmintokens == "/tokens"
        assert s.capture_alt_formats is False
        assert s.garminconnect_is_cn is False
