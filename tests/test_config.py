"""Tests for environment-driven configuration."""

from __future__ import annotations

from garmincapture.config import DEFAULT_FETCH_EXCLUDE, Settings


class TestCaptureEnabled:
    def test_disabled_when_unset(self):
        assert Settings(BRONZE_ROOT="").capture_enabled is False

    def test_disabled_when_whitespace(self):
        assert Settings(BRONZE_ROOT="   ").capture_enabled is False

    def test_enabled_when_set(self):
        assert Settings(BRONZE_ROOT="/data/bronze").capture_enabled is True


class TestFetchSelection:
    def test_empty_selection_selects_everything(self):
        # Clear the default exclude too, so "everything" really means everything.
        s = Settings(FETCH_SELECTION="", FETCH_EXCLUDE="")
        assert s.is_selected("sleep") is True
        assert s.is_selected("anything") is True

    def test_subset_selection(self):
        s = Settings(FETCH_SELECTION="sleep, activities ,hrv")
        assert s.fetch_selection_set == {"sleep", "activities", "hrv"}
        assert s.is_selected("sleep") is True
        assert s.is_selected("floors") is False


class TestFetchExclude:
    def test_default_excludes_female_health(self):
        s = Settings()
        assert s.fetch_exclude_set == {
            "menstrual_day", "menstrual_calendar", "pregnancy_summary"
        }
        assert s.is_selected("menstrual_day") is False
        assert s.is_selected("menstrual_calendar") is False
        assert s.is_selected("pregnancy_summary") is False
        # Everything else still runs.
        assert s.is_selected("sleep") is True

    def test_empty_exclude_captures_everything(self):
        s = Settings(FETCH_EXCLUDE="")
        assert s.fetch_exclude_set == set()
        assert s.is_selected("menstrual_day") is True

    def test_custom_exclude_replaces_default(self):
        s = Settings(FETCH_EXCLUDE="floors, spo2")
        assert s.fetch_exclude_set == {"floors", "spo2"}
        assert s.is_selected("floors") is False
        # The default female-health endpoints are no longer excluded.
        assert s.is_selected("menstrual_day") is True

    def test_exclude_overrides_explicit_selection(self):
        # Even if a name is explicitly selected, exclusion wins.
        s = Settings(FETCH_SELECTION="sleep,floors", FETCH_EXCLUDE="floors")
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
        assert s.fetch_exclude == DEFAULT_FETCH_EXCLUDE
