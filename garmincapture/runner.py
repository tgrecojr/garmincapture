"""Orchestration — window iteration, fan-out, rate limiting, capture.

Drives the whole pull from the allowlisted catalog. Capture is best-effort and
non-fatal: a bronze write failure, or a per-endpoint pull error, is logged and
the loop continues. The run halts only on authentication or rate-limit (429)
errors, where hammering would be harmful.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

from . import catalog
from .bronze import (
    MODE_RAW,
    MODE_RESERIALIZED,
    CaptureMeta,
    capture_bronze,
)
from .catalog import (
    GOAL_STATUSES,
    KIND_DAILY,
    KIND_RANGE,
    KIND_STATIC,
    KIND_STATIC_GOALS,
    KIND_WEEKLY,
    Endpoint,
)
from .config import Settings
from .logging_config import get_logger
from .serialize import screen_for_secrets, to_bronze_json

log = get_logger(__name__)

# Content-type map for binary download formats (raw grade).
_DOWNLOAD_EXT = {
    "ORIGINAL": ("zip", "application/zip"),
    "TCX": ("tcx", "application/vnd.garmin.tcx+xml"),
    "GPX": ("gpx", "application/gpx+xml"),
    "KML": ("kml", "application/vnd.google-earth.kml+xml"),
    "CSV": ("csv", "text/csv"),
}


@dataclass(frozen=True, slots=True)
class Window:
    """A pull window. ``days()`` is reverse-chronological (newest first)."""

    start: date
    end: date

    def days(self) -> list[str]:
        out: list[str] = []
        cur = self.end
        while cur >= self.start:
            out.append(cur.isoformat())
            cur -= timedelta(days=1)
        return out

    def range(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()

    @property
    def end_str(self) -> str:
        return self.end.isoformat()


def build_window(
    settings: Settings,
    *,
    today: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Window:
    """Build the pull window: explicit ``start``/``end`` (backfill) or a trailing
    ``LOOKBACK_DAYS`` window ending today (incremental)."""
    today = today or datetime.now(UTC).date()
    if start is not None or end is not None:
        w_end = end or today
        w_start = start or w_end
        if w_start > w_end:
            raise ValueError(
                f"backfill start ({w_start.isoformat()}) is after end ({w_end.isoformat()})"
            )
        return Window(start=w_start, end=w_end)
    lookback = max(settings.lookback_days - 1, 0)
    return Window(start=today - timedelta(days=lookback), end=today)


class Runner:
    """Executes a pull for a given window against a live Garmin client."""

    def __init__(self, settings: Settings, client: Garmin):
        self.settings = settings
        self.client = client
        self.captured = 0
        self.skipped = 0
        self.failed = 0

    # -- low-level capture helper --------------------------------------------
    def _capture_json(
        self,
        ep_name: str,
        collection: str,
        parsed,
        request_params: dict,
        *,
        screen_secrets: bool = False,
    ) -> None:
        """Screen (if asked), serialize, and capture a parsed JSON return."""
        redacted: list[str] = []
        payload = parsed
        if screen_secrets:
            payload, redacted = screen_for_secrets(parsed)
        body = to_bronze_json(payload)
        meta = CaptureMeta(
            ext="json",
            capture_mode=MODE_RESERIALIZED,
            request_url=ep_name,
            request_params=request_params,
            content_type="application/json",
            charset="utf-8",
            redacted_fields=redacted,
        )
        result = capture_bronze(
            "garmin", collection, body, meta,
            bronze_root=self.settings.bronze_root,
            processor_version=self.settings.processor_version,
        )
        if result is not None:
            self.captured += 1

    def _safe(self, ep: Endpoint, fn, request_params: dict):
        """Call ``fn`` (a no-arg closure), capture the result, return parsed.

        None / empty returns are skipped (no bronze file). Auth/429 errors are
        re-raised to stop the run; all others are logged non-fatally.
        """
        try:
            parsed = fn()
            if parsed is None:
                if ep.skip_if_none:
                    self.skipped += 1
                return None
            if ep.skip_if_empty and not parsed:
                self.skipped += 1
                return parsed
            self._capture_json(
                ep.method, ep.collection, parsed, request_params,
                screen_secrets=ep.screen_secrets,
            )
            return parsed
        except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError):
            raise
        except Exception as exc:  # noqa: BLE001 — capture is non-fatal
            self.failed += 1
            log.warning("pull_failed", collection=ep.collection, error=str(exc))
            return None

    def _pace(self) -> None:
        if self.settings.rate_limit_seconds > 0:
            time.sleep(self.settings.rate_limit_seconds)

    # -- bucket runners ------------------------------------------------------
    def _run_static(self, window: Window) -> dict[str, object]:
        """Bucket C reference/metadata. Returns parsed results keyed by name."""
        results: dict[str, object] = {}
        for ep in catalog.by_kind(KIND_STATIC):
            if not self.settings.is_selected(ep.name):
                continue
            results[ep.name] = self._safe(ep, lambda ep=ep: getattr(self.client, ep.method)(), {})
            self._pace()

        # Goals: one call per status, all into the same collection.
        goals_ep = catalog.get("goals")
        if goals_ep and self.settings.is_selected("goals"):
            for status in GOAL_STATUSES:
                self._safe(
                    goals_ep,
                    lambda s=status: getattr(self.client, goals_ep.method)(status=s),
                    {"status": status},
                )
                self._pace()

        self._run_per_profile(results.get("userprofile_settings"))
        self._run_per_device(window, results.get("devices"))
        return results

    def _run_per_profile(self, profile_settings) -> None:
        """Gear endpoints, keyed by the user's profile number (PK)."""
        profile_number = None
        if isinstance(profile_settings, dict):
            profile_number = profile_settings.get("id")
        if profile_number is None:
            log.debug("per_profile_skipped", reason="no userProfileNumber")
            return
        for ep in catalog._PER_PROFILE:  # noqa: SLF001 — internal grouping
            if not self.settings.is_selected(ep.name):
                continue
            self._safe(
                ep,
                lambda ep=ep: getattr(self.client, ep.method)(profile_number),
                {"user_profile_number": profile_number},
            )
            self._pace()

    def _run_per_device(self, window: Window, devices) -> None:
        """Per-device settings and (range) solar data."""
        if not isinstance(devices, list):
            return
        window_start, window_end = window.range()
        for dev in devices:
            device_id = dev.get("deviceId") if isinstance(dev, dict) else None
            if device_id is None:
                continue
            settings_ep = catalog.get("device_settings")
            if settings_ep and self.settings.is_selected("device_settings"):
                self._safe(
                    settings_ep,
                    lambda did=device_id: getattr(self.client, settings_ep.method)(did),
                    {"device_id": device_id},
                )
                self._pace()
            solar_ep = catalog.get("device_solar")
            if solar_ep and self.settings.is_selected("device_solar"):
                self._safe(
                    solar_ep,
                    lambda did=device_id: getattr(self.client, solar_ep.method)(
                        str(did), window_start, window_end
                    ),
                    {"device_id": device_id, "start": window_start, "end": window_end},
                )
                self._pace()

    def _run_daily(self, window: Window) -> None:
        for cdate in window.days():
            for ep in catalog.by_kind(KIND_DAILY):
                if not self.settings.is_selected(ep.name):
                    continue
                self._safe(
                    ep,
                    lambda ep=ep, d=cdate: getattr(self.client, ep.method)(d),
                    {"cdate": cdate},
                )
                self._pace()

    def _run_range_and_weekly(self, window: Window) -> None:
        start, end = window.range()
        for ep in catalog.by_kind(KIND_RANGE):
            if ep.name == "activities":  # handled by the fan-out runner
                continue
            if not self.settings.is_selected(ep.name):
                continue
            self._safe(
                ep,
                lambda ep=ep: getattr(self.client, ep.method)(start, end),
                {"start": start, "end": end},
            )
            self._pace()
        for ep in catalog.by_kind(KIND_WEEKLY):
            if not self.settings.is_selected(ep.name):
                continue
            self._safe(
                ep,
                lambda ep=ep: getattr(self.client, ep.method)(end, self.settings.weekly_weeks),
                {"end": end, "weeks": self.settings.weekly_weeks},
            )
            self._pace()

    def _run_activities(self, window: Window) -> None:
        """Discover activities for the window, then fan out per activity."""
        activities_ep = catalog.get("activities")
        if not activities_ep or not self.settings.is_selected("activities"):
            return
        start, end = window.range()
        activities = self._safe(
            activities_ep,
            lambda: self.client.get_activities_by_date(start, end),
            {"start": start, "end": end},
        )
        for act in activities or []:
            aid = act.get("activityId") if isinstance(act, dict) else None
            if aid is None:
                continue
            self._fan_out_activity(aid)

    def _fan_out_activity(self, aid) -> None:
        for name, method in catalog.PER_ACTIVITY:
            if not self.settings.is_selected(name):
                continue
            ep = Endpoint(name, method, KIND_STATIC, name)
            self._safe(
                ep,
                lambda m=method, a=aid: getattr(self.client, m)(a),
                {"activity_id": aid},
            )
            self._pace()

        downloads = list(catalog.PER_ACTIVITY_DOWNLOAD)
        if self.settings.capture_alt_formats:
            downloads += list(catalog.PER_ACTIVITY_ALT_DOWNLOAD)
        for collection, fmt_name in downloads:
            if not self.settings.is_selected(collection):
                continue
            self._download_activity(aid, collection, fmt_name)
            self._pace()

    def _download_activity(self, aid, collection: str, fmt_name: str) -> None:
        """Capture a binary activity download at raw grade (true bytes)."""
        try:
            fmt = getattr(Garmin.ActivityDownloadFormat, fmt_name)
            raw = self.client.download_activity(str(aid), fmt)
            if not raw:
                self.skipped += 1
                return
            ext, content_type = _DOWNLOAD_EXT.get(fmt_name, ("bin", "application/octet-stream"))
            meta = CaptureMeta(
                ext=ext,
                capture_mode=MODE_RAW,
                request_url="download_activity",
                request_params={"activity_id": aid, "format": fmt_name},
                content_type=content_type,
                charset=None,
                content_encoding="identity",
                stored_encoding="identity",
            )
            result = capture_bronze(
                "garmin", collection, raw, meta,
                bronze_root=self.settings.bronze_root,
                processor_version=self.settings.processor_version,
            )
            if result is not None:
                self.captured += 1
        except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError):
            raise
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            log.warning("download_failed", collection=collection, activity_id=aid, error=str(exc))

    # -- entrypoint ----------------------------------------------------------
    def run(self, window: Window) -> dict[str, int]:
        drift = catalog.detect_catalog_drift(Garmin)
        if drift:
            log.warning(
                "catalog_drift",
                new_readable_endpoints=drift,
                hint="library upgrade exposed endpoints not in CATALOG; review and add",
            )
        log.info("pull_start", start=window.start.isoformat(), end=window.end.isoformat())

        self._run_static(window)
        self._run_daily(window)
        self._run_range_and_weekly(window)
        self._run_activities(window)

        stats = {"captured": self.captured, "skipped": self.skipped, "failed": self.failed}
        log.info("pull_complete", **stats)
        return stats
