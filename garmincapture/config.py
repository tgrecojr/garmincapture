"""Environment-driven configuration for garmincapture.

All knobs come from the environment (mirroring garmin-grafana / glucose-loader).
The single most important one is ``BRONZE_ROOT``: if it is unset or empty, the
bronze capture layer is a complete noop (see ``bronze.capture_bronze``), per the
Bronze Layer Specification.

Nothing in here ever holds a credential beyond the login secrets needed to hand
to the ``garminconnect`` library; those are never written to bronze.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration parsed from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Bronze sink -------------------------------------------------------
    # Unset/empty => capture is a complete noop (single early guard).
    bronze_root: str = Field(default="", alias="BRONZE_ROOT")

    # --- Garmin auth (delegated to the library) ----------------------------
    garmintokens: str = Field(default="/tokens", alias="GARMINTOKENS")
    garminconnect_email: str = Field(default="", alias="GARMINCONNECT_EMAIL")
    garminconnect_base64_password: str = Field(
        default="", alias="GARMINCONNECT_BASE64_PASSWORD"
    )
    garminconnect_is_cn: bool = Field(default=False, alias="GARMINCONNECT_IS_CN")

    # --- Pull window -------------------------------------------------------
    # 7-day trailing overlap: comfortably covers Garmin's restatement lag and
    # survives a few missed scheduled runs with no gaps. Backfill is a separate,
    # explicit invocation via --start/--end (which overrides this entirely).
    lookback_days: int = Field(default=7, alias="LOOKBACK_DAYS")

    # --- Pacing / resilience ----------------------------------------------
    rate_limit_seconds: float = Field(default=2.0, alias="RATE_LIMIT_SECONDS")

    # --- Polling (always-on internal loop) --------------------------------
    # Seconds to sleep between incremental pulls. Default 15 minutes. Only used
    # in incremental mode; backfill (--start/--end) and --once run exactly once.
    poll_interval_seconds: int = Field(default=900, alias="POLL_INTERVAL_SECONDS")

    # --- Selection / toggles ----------------------------------------------
    # Comma-separated subset of catalog names; empty => everything in A/B/C.
    fetch_selection: str = Field(default="", alias="FETCH_SELECTION")
    capture_alt_formats: bool = Field(default=False, alias="CAPTURE_ALT_FORMATS")

    # --- Provenance stamping ----------------------------------------------
    processor_version: str = Field(default="dev", alias="PROCESSOR_VERSION")

    # --- Aggregate-window sizing (weekly endpoints) ------------------------
    # Trailing weeks requested from the weekly-aggregate endpoints
    # (get_weekly_steps/get_weekly_stress). 4 keeps the trailing month of weekly
    # rollups fresh as they settle. Note: NOT widened by --start/--end backfills;
    # bump this for the one-off invocation if you want deeper weekly history.
    weekly_weeks: int = Field(default=4, alias="WEEKLY_WEEKS")

    @property
    def capture_enabled(self) -> bool:
        """True when a bronze root is configured (non-empty)."""
        return bool(self.bronze_root and self.bronze_root.strip())

    @property
    def fetch_selection_set(self) -> set[str]:
        """Parsed ``FETCH_SELECTION`` as a set of catalog names (empty => all)."""
        return {
            name.strip()
            for name in self.fetch_selection.split(",")
            if name.strip()
        }

    def is_selected(self, name: str) -> bool:
        """Whether a catalog endpoint ``name`` should run this invocation."""
        selection = self.fetch_selection_set
        return not selection or name in selection


def load_settings() -> Settings:
    """Load settings from the environment. Kept as a function for easy mocking."""
    return Settings()
