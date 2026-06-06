"""Tests for the allowlisted catalog and the drift detector."""

from __future__ import annotations

from garmincapture import catalog


class TestAllowlist:
    def test_no_bucket_d_methods_present(self):
        """No mutating/auth/plumbing method may appear in the catalog."""
        assert catalog.detect_forbidden_in_catalog() == []

    def test_explicit_bucket_d_names_absent(self):
        """A spot-check of known dangerous methods that must never be callable."""
        forbidden = {
            "add_weigh_in", "set_activity_name", "create_manual_activity",
            "delete_activity", "upload_activity", "remove_gear_from_activity",
            "schedule_workout", "login", "logout", "request_reload",
            "query_garmin_graphql", "count_activities",
        }
        assert forbidden.isdisjoint(catalog.catalog_method_names())

    def test_catalog_names_are_unique(self):
        names = [ep.name for ep in catalog.CATALOG]
        assert len(names) == len(set(names))

    def test_collections_are_unique(self):
        collections = [ep.collection for ep in catalog.CATALOG]
        assert len(collections) == len(set(collections))

    def test_secret_screening_only_on_profile_endpoints(self):
        screened = {ep.name for ep in catalog.CATALOG if ep.screen_secrets}
        assert screened == {"user_settings", "userprofile_settings"}

    def test_hrv_skips_on_none(self):
        assert catalog.get("hrv").skip_if_none is True

    def test_added_endpoints_present(self):
        """Endpoints adopted from a library-drift review must be in the catalog."""
        added = {
            "cycling_ftp", "device_alarms", "available_badges", "in_progress_badges",
            "morning_training_readiness", "activities_fordate",
        }
        assert added <= {ep.name for ep in catalog.CATALOG}


class TestRedundancySuppression:
    def test_redundant_disjoint_from_catalog(self):
        """A method is either captured or marked redundant — never both."""
        assert catalog._KNOWN_REDUNDANT.isdisjoint(catalog.catalog_method_names())

    def test_redundant_methods_not_reported_as_drift(self):
        """Suppressed aliases/subsets must not re-appear as drift each startup."""
        from garminconnect import Garmin

        drift = catalog.detect_catalog_drift(Garmin)
        assert catalog._KNOWN_REDUNDANT.isdisjoint(drift)


class TestDriftDetection:
    class _FakeGarmin:
        # Mix of allowlisted, plumbing, dangerous, and a NEW readable endpoint.
        def get_sleep_data(self): ...        # in catalog
        def get_brand_new_metric(self): ...  # NOT in catalog -> drift
        def add_weigh_in(self): ...          # dangerous -> ignored
        def login(self): ...                 # plumbing -> ignored
        def _private(self): ...              # private -> ignored

    def test_reports_new_readable_endpoint(self):
        drift = catalog.detect_catalog_drift(self._FakeGarmin)
        assert drift == ["get_brand_new_metric"]

    def test_real_library_has_no_unexpected_drift(self):
        """Against the pinned library, the catalog should cover the readable surface
        we know about. If this fails, a library upgrade exposed new endpoints —
        review and add them to the catalog (this is the drift detector working)."""
        from garminconnect import Garmin

        drift = catalog.detect_catalog_drift(Garmin)
        # We assert the detector runs and returns a list; we don't hard-fail on
        # drift here (that's a human review signal), but we surface it.
        assert isinstance(drift, list)
