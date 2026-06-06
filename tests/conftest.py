"""Shared pytest fixtures for garmincapture tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from garmincapture.config import Settings


@pytest.fixture(autouse=True)
def _ignore_local_dotenv(monkeypatch):
    """Tests must never read a developer's local ``.env``; force defaults."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def bronze_settings(tmp_path):
    """Settings with capture enabled to a temp bronze root and no inter-call sleep."""
    return Settings(
        BRONZE_ROOT=str(tmp_path / "bronze"),
        GARMINTOKENS=str(tmp_path / "tokens"),
        RATE_LIMIT_SECONDS=0,
        PROCESSOR_VERSION="test-1.2.3",
        LOOKBACK_DAYS=2,
    )


@pytest.fixture
def disabled_settings():
    """Settings with BRONZE_ROOT unset => capture is a complete noop."""
    return Settings(BRONZE_ROOT="", RATE_LIMIT_SECONDS=0)


@pytest.fixture
def fixed_today():
    return date(2026, 6, 6)


@pytest.fixture
def mock_client():
    """A MagicMock standing in for a logged-in Garmin client.

    Every catalog method returns a small JSON-able object by default; tests
    override specific ones as needed.
    """
    from garmincapture import catalog

    client = MagicMock()

    # Every catalog method (and the per-activity fan-out) returns a small,
    # JSON-able dict by default. We drive this from the catalog itself so the
    # mock can't silently fall back to a non-serializable child mock.
    def _generic(*args, **kwargs):
        return {"ok": True, "args": [str(a) for a in args]}

    known = set(catalog.catalog_method_names())
    known.update({"get_devices", "get_userprofile_settings", "get_user_profile"})
    for name in known:
        if name == "download_activity":
            continue
        getattr(client, name).side_effect = _generic

    # Targeted returns the runner relies on.
    client.get_devices.side_effect = None
    client.get_devices.return_value = [{"deviceId": 111, "productDisplayName": "Forerunner"}]
    client.get_userprofile_settings.side_effect = None
    client.get_userprofile_settings.return_value = {"id": 42, "displayName": "tester"}
    client.get_user_profile.side_effect = None
    client.get_user_profile.return_value = {
        "userData": {"weight": 80000},
        "accessToken": "SECRET-SHOULD-BE-REDACTED",
    }
    client.get_activities_by_date.side_effect = None
    client.get_activities_by_date.return_value = [{"activityId": 9001, "activityName": "Run"}]
    client.get_hrv_data.side_effect = None
    client.get_hrv_data.return_value = None  # skip_if_none path
    client.download_activity.return_value = b"PK\x03\x04fake-zip-bytes"

    return client
