"""Declarative, allowlisted endpoint registry.

The whole pull is driven from this registry so that "capture everything" is
auditable and Bucket D (writes/deletes/auth/plumbing) is impossible to call by
accident. Only *data* endpoints appear here; mutating/auth/plumbing methods are
simply absent.

Allowlist vs denylist
---------------------
We deliberately use an allowlist, for two reasons:

1. The registry is not just a filter — it is the *call recipe*. The library's
   read methods have wildly non-uniform signatures (``get_user_summary(cdate)``
   vs ``get_weekly_steps(end, weeks)`` vs ``get_device_solar_data(device_id,
   start, end)``), so a newly added endpoint can't be invoked correctly without
   per-method knowledge anyway. A denylist would not give "free" capture.
2. The risk is asymmetric. Missing a new *read* endpoint until we add it is
   cheap and self-correcting (bronze is append-only). Accidentally calling a
   new *mutating* endpoint, or capturing a new secret-bearing payload, is
   irreversible. "Unknown ⇒ skip" is the correct default for immutable storage.

To recover the one genuine benefit of a denylist — never silently missing new
free data — ``detect_catalog_drift`` introspects the library on startup and
loudly reports any readable endpoint not covered here. See the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Call kinds --------------------------------------------------------------
# How the runner builds arguments for an endpoint's method.
KIND_DAILY = "daily"               # method(cdate)
KIND_RANGE = "range"               # method(start, end)
KIND_WEEKLY = "weekly"             # method(end, weeks)
KIND_STATIC = "static"             # method()
KIND_STATIC_GOALS = "static_goals" # method(status) for each status
KIND_PER_PROFILE = "per_profile"   # method(user_profile_number)
KIND_PER_DEVICE = "per_device"     # method(device_id)
KIND_PER_DEVICE_RANGE = "per_device_range"  # method(device_id, start, end)

GOAL_STATUSES = ("active", "past", "future")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One allowlisted data endpoint.

    Attributes:
        name: Logical, stable identifier (also the ``FETCH_SELECTION`` token).
        method: The ``garminconnect.Garmin`` method name to call.
        kind: Argument-building convention (one of the ``KIND_*`` constants).
        collection: Bronze collection (under ``garmin/``) to write into.
        skip_if_none: Treat a ``None`` return as "no data" — write nothing.
        skip_if_empty: Treat an empty list/dict return as "no data" — write nothing.
        screen_secrets: Run secret-screening before capture (profile/settings only).
    """

    name: str
    method: str
    kind: str
    collection: str
    skip_if_none: bool = False
    skip_if_empty: bool = False
    screen_secrets: bool = False


# --- Bucket A: date-partitioned daily/range metrics --------------------------
_DAILY: tuple[Endpoint, ...] = (
    Endpoint("user_summary", "get_user_summary", KIND_DAILY, "user_summary"),
    Endpoint("steps_intraday", "get_steps_data", KIND_DAILY, "steps_intraday"),
    Endpoint("floors", "get_floors", KIND_DAILY, "floors"),
    Endpoint("heart_rates", "get_heart_rates", KIND_DAILY, "heart_rates"),
    Endpoint("resting_heart_rate", "get_rhr_day", KIND_DAILY, "resting_heart_rate"),
    Endpoint("body_battery_events", "get_body_battery_events", KIND_DAILY, "body_battery_events"),
    Endpoint("sleep", "get_sleep_data", KIND_DAILY, "sleep"),
    Endpoint("stress", "get_all_day_stress", KIND_DAILY, "stress"),
    Endpoint("daily_events", "get_all_day_events", KIND_DAILY, "daily_events"),
    Endpoint("respiration", "get_respiration_data", KIND_DAILY, "respiration"),
    Endpoint("spo2", "get_spo2_data", KIND_DAILY, "spo2"),
    Endpoint("intensity_minutes", "get_intensity_minutes_data", KIND_DAILY, "intensity_minutes"),
    Endpoint("hydration", "get_hydration_data", KIND_DAILY, "hydration"),
    Endpoint("hrv", "get_hrv_data", KIND_DAILY, "hrv", skip_if_none=True),
    Endpoint("training_readiness", "get_training_readiness", KIND_DAILY, "training_readiness"),
    Endpoint("training_status", "get_training_status", KIND_DAILY, "training_status"),
    Endpoint("max_metrics", "get_max_metrics", KIND_DAILY, "max_metrics"),  # VO2max
    Endpoint("fitness_age", "get_fitnessage_data", KIND_DAILY, "fitness_age"),
    Endpoint("daily_weigh_ins", "get_daily_weigh_ins", KIND_DAILY, "daily_weigh_ins"),
    Endpoint("lifestyle_logging", "get_lifestyle_logging_data", KIND_DAILY, "lifestyle_logging", skip_if_empty=True),
    Endpoint("menstrual_day", "get_menstrual_data_for_date", KIND_DAILY, "menstrual_day", skip_if_empty=True),
    Endpoint("nutrition_food_log", "get_nutrition_daily_food_log", KIND_DAILY, "nutrition_food_log", skip_if_empty=True),
    Endpoint("nutrition_meals", "get_nutrition_daily_meals", KIND_DAILY, "nutrition_meals", skip_if_empty=True),
    Endpoint("nutrition_settings", "get_nutrition_daily_settings", KIND_DAILY, "nutrition_settings", skip_if_empty=True),
)

_RANGE: tuple[Endpoint, ...] = (
    Endpoint("daily_steps", "get_daily_steps", KIND_RANGE, "daily_steps"),
    Endpoint("body_battery", "get_body_battery", KIND_RANGE, "body_battery"),
    Endpoint("endurance_score", "get_endurance_score", KIND_RANGE, "endurance_score"),
    Endpoint("hill_score", "get_hill_score", KIND_RANGE, "hill_score"),
    Endpoint("blood_pressure", "get_blood_pressure", KIND_RANGE, "blood_pressure"),
    Endpoint("body_composition", "get_body_composition", KIND_RANGE, "body_composition"),
    Endpoint("weigh_ins", "get_weigh_ins", KIND_RANGE, "weigh_ins"),
    Endpoint("running_tolerance", "get_running_tolerance", KIND_RANGE, "running_tolerance"),
    Endpoint("weekly_intensity_minutes", "get_weekly_intensity_minutes", KIND_RANGE, "weekly_intensity_minutes"),
    Endpoint("menstrual_calendar", "get_menstrual_calendar_data", KIND_RANGE, "menstrual_calendar", skip_if_empty=True),
    # Drives the per-activity fan-out; also captured as a merged list.
    Endpoint("activities", "get_activities_by_date", KIND_RANGE, "activities"),
)

_WEEKLY: tuple[Endpoint, ...] = (
    Endpoint("weekly_steps", "get_weekly_steps", KIND_WEEKLY, "weekly_steps"),
    Endpoint("weekly_stress", "get_weekly_stress", KIND_WEEKLY, "weekly_stress"),
)

# --- Bucket C: reference / metadata (own collections, low frequency) ---------
_STATIC: tuple[Endpoint, ...] = (
    Endpoint("race_predictions", "get_race_predictions", KIND_STATIC, "race_predictions"),
    Endpoint("lactate_threshold", "get_lactate_threshold", KIND_STATIC, "lactate_threshold"),
    Endpoint("pregnancy_summary", "get_pregnancy_summary", KIND_STATIC, "pregnancy_summary", skip_if_empty=True),
    Endpoint("devices", "get_devices", KIND_STATIC, "devices"),
    Endpoint("device_last_used", "get_device_last_used", KIND_STATIC, "device_last_used"),
    Endpoint("primary_device", "get_primary_training_device", KIND_STATIC, "primary_device"),
    Endpoint("user_settings", "get_user_profile", KIND_STATIC, "user_settings", screen_secrets=True),
    Endpoint("userprofile_settings", "get_userprofile_settings", KIND_STATIC, "userprofile_settings", screen_secrets=True),
    Endpoint("activity_types", "get_activity_types", KIND_STATIC, "activity_types"),
    Endpoint("personal_records", "get_personal_record", KIND_STATIC, "personal_records"),
    Endpoint("training_plans", "get_training_plans", KIND_STATIC, "training_plans"),
    Endpoint("workouts", "get_workouts", KIND_STATIC, "workouts"),
    Endpoint("earned_badges", "get_earned_badges", KIND_STATIC, "earned_badges", skip_if_empty=True),
    Endpoint("goals", "get_goals", KIND_STATIC_GOALS, "goals"),
)

# Need an id discovered from another call (handled explicitly by the runner).
_PER_PROFILE: tuple[Endpoint, ...] = (
    Endpoint("gear", "get_gear", KIND_PER_PROFILE, "gear", skip_if_empty=True),
    Endpoint("gear_defaults", "get_gear_defaults", KIND_PER_PROFILE, "gear_defaults", skip_if_empty=True),
)

_PER_DEVICE: tuple[Endpoint, ...] = (
    Endpoint("device_settings", "get_device_settings", KIND_PER_DEVICE, "device_settings"),
    Endpoint("device_solar", "get_device_solar_data", KIND_PER_DEVICE_RANGE, "device_solar", skip_if_empty=True),
)

# The full registry, in a stable order.
CATALOG: tuple[Endpoint, ...] = _DAILY + _RANGE + _WEEKLY + _STATIC + _PER_PROFILE + _PER_DEVICE

# --- Bucket B: per-activity fan-out ------------------------------------------
# Applied to every activityId discovered from the ``activities`` pull.
# (name -> garminconnect method); each returns one object => one bronze file.
PER_ACTIVITY: tuple[tuple[str, str], ...] = (
    ("activity_summary", "get_activity"),
    ("activity_details", "get_activity_details"),
    ("activity_splits", "get_activity_splits"),
    ("activity_typed_splits", "get_activity_typed_splits"),
    ("activity_split_summaries", "get_activity_split_summaries"),
    ("activity_weather", "get_activity_weather"),
    ("activity_hr_zones", "get_activity_hr_in_timezones"),
    ("activity_power_zones", "get_activity_power_in_timezones"),
    ("activity_exercise_sets", "get_activity_exercise_sets"),
    ("activity_gear", "get_activity_gear"),
)

# Binary downloads (raw grade). The FIT/original is the crown-jewel artifact.
# (collection, ActivityDownloadFormat member name)
PER_ACTIVITY_DOWNLOAD: tuple[tuple[str, str], ...] = (
    ("activity_fit", "ORIGINAL"),
)

# Optional alternate export formats, enabled by CAPTURE_ALT_FORMATS.
PER_ACTIVITY_ALT_DOWNLOAD: tuple[tuple[str, str], ...] = (
    ("activity_tcx", "TCX"),
    ("activity_gpx", "GPX"),
    ("activity_kml", "KML"),
    ("activity_csv", "CSV"),
)


# --- Grouping helpers --------------------------------------------------------
def by_kind(*kinds: str) -> tuple[Endpoint, ...]:
    """Return catalog endpoints matching any of ``kinds``, in catalog order."""
    return tuple(ep for ep in CATALOG if ep.kind in kinds)


def get(name: str) -> Endpoint | None:
    """Look up a catalog endpoint by its logical name."""
    for ep in CATALOG:
        if ep.name == name:
            return ep
    return None


def catalog_method_names() -> set[str]:
    """Every Garmin method name this catalog (and the fan-out) will ever call."""
    names = {ep.method for ep in CATALOG}
    names.update(method for _, method in PER_ACTIVITY)
    names.add("download_activity")  # used for all PER_ACTIVITY_DOWNLOAD
    return names


# --- Drift detection (the "denylist benefit" without the denylist risk) ------
# Method-name prefixes/names that mutate state, authenticate, or are plumbing.
# Used ONLY to classify methods for drift reporting — never to decide what to
# call (the allowlist does that).
_DANGEROUS_PREFIXES: tuple[str, ...] = (
    "add_", "set_", "create_", "update_", "upload_", "delete_", "remove_",
    "schedule_", "unschedule_", "reset_", "sync_", "archive_", "merge_",
    "post_", "put_", "patch_", "edit_", "save_", "import_",
)
_KNOWN_PLUMBING: frozenset[str] = frozenset({
    "login", "logout", "resume_login", "request_reload", "query_garmin_graphql",
    "count_activities", "get_unit_system", "get_full_name", "garth", "connectapi",
    "download", "modern_url", "garmin_connect_user_settings",
})


def _is_readable_candidate(name: str) -> bool:
    """A public method that *looks* like a data getter we might want to capture."""
    if name.startswith("_"):
        return False
    if name in _KNOWN_PLUMBING:
        return False
    if any(name.startswith(p) for p in _DANGEROUS_PREFIXES):
        return False
    return name.startswith(("get_", "download_"))


def detect_catalog_drift(garmin_cls) -> list[str]:
    """Report readable library methods not covered by the catalog.

    Introspects ``garmin_cls`` (the ``garminconnect.Garmin`` class) for public
    ``get_*``/``download_*`` methods that are neither in our allowlist nor in the
    known plumbing/dangerous sets. The result is a list of endpoints a library
    upgrade may have exposed "for free" that we are *not yet* capturing — surfaced
    loudly so a human can review and add them, without ever auto-calling them.
    """
    known = catalog_method_names()
    candidates = {
        name
        for name in dir(garmin_cls)
        if callable(getattr(garmin_cls, name, None)) and _is_readable_candidate(name)
    }
    return sorted(candidates - known)


def detect_forbidden_in_catalog() -> list[str]:
    """Sanity check: any catalog method that looks mutating/plumbing (should be empty).

    Backs the unit test that guarantees no Bucket D method ever sneaks into the
    allowlist.
    """
    offenders = []
    for method in catalog_method_names():
        if method in _KNOWN_PLUMBING or any(method.startswith(p) for p in _DANGEROUS_PREFIXES):
            offenders.append(method)
    return sorted(offenders)
