"""Tests for the bronze write contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from garmincapture.bronze import (
    MODE_RAW,
    MODE_RESERIALIZED,
    CaptureMeta,
    capture_bronze,
)


def _json_meta(**kw):
    return CaptureMeta(
        ext="json",
        capture_mode=MODE_RESERIALIZED,
        request_url="get_sleep_data",
        request_params={"cdate": "2026-06-06"},
        **kw,
    )


class TestNoop:
    def test_unset_bronze_root_is_complete_noop(self, tmp_path):
        result = capture_bronze(
            "garmin", "sleep", b'{"x":1}', _json_meta(), bronze_root="", processor_version="v",
        )
        assert result is None
        # Nothing created anywhere.
        assert list(tmp_path.iterdir()) == []

    def test_none_bronze_root_is_noop(self):
        assert capture_bronze(
            "garmin", "sleep", b"{}", _json_meta(), bronze_root=None,
        ) is None


class TestPathAndNaming:
    def test_path_standard_and_sidecar_present(self, tmp_path):
        root = str(tmp_path)
        payload = capture_bronze(
            "garmin", "sleep", b'{"x":1}', _json_meta(),
            bronze_root=root, processor_version="v1.0",
        )
        p = Path(payload)
        # {root}/garmin/sleep/dt=YYYY-MM-DD/sleep_<ms>_<id>.json
        assert p.parent.parent.name == "sleep"
        assert p.parent.parent.parent.name == "garmin"
        assert p.parent.name.startswith("dt=")
        assert p.name.startswith("sleep_") and p.suffix == ".json"
        sidecar = Path(str(p) + ".meta.json")
        assert sidecar.exists()

    def test_filenames_are_unique(self, tmp_path):
        root = str(tmp_path)
        paths = {
            capture_bronze("garmin", "sleep", b"{}", _json_meta(), bronze_root=root)
            for _ in range(25)
        }
        assert len(paths) == 25


class TestSidecarFields:
    def test_sidecar_has_all_required_fields(self, tmp_path):
        payload = capture_bronze(
            "garmin", "sleep", b'{"x":1}',
            _json_meta(redacted_fields=["accessToken"]),
            bronze_root=str(tmp_path), processor_version="v9",
        )
        sidecar = json.loads(Path(payload + ".meta.json").read_text())
        for key in (
            "source", "collection", "fetched_at", "fetched_at_unix_ms",
            "request_url", "request_params", "http_status", "content_type",
            "charset", "content_encoding", "stored_encoding", "capture_mode",
            "byte_size", "sha256", "redacted_fields", "processor",
            "processor_version", "schema_version",
        ):
            assert key in sidecar, f"missing sidecar field: {key}"
        assert sidecar["source"] == "garmin"
        assert sidecar["collection"] == "sleep"
        assert sidecar["capture_mode"] == MODE_RESERIALIZED
        assert sidecar["processor"] == "garmincapture"
        assert sidecar["processor_version"] == "v9"
        assert sidecar["schema_version"] == "v1"
        assert sidecar["redacted_fields"] == ["accessToken"]
        assert sidecar["http_status"] == 200

    def test_hash_and_size_match_stored_bytes(self, tmp_path):
        body = b'{"hello":"world"}'
        payload = capture_bronze(
            "garmin", "sleep", body, _json_meta(), bronze_root=str(tmp_path),
        )
        on_disk = Path(payload).read_bytes()
        assert on_disk == body
        sidecar = json.loads(Path(payload + ".meta.json").read_text())
        assert sidecar["byte_size"] == len(body)
        assert sidecar["sha256"] == hashlib.sha256(body).hexdigest()


class TestRawGrade:
    def test_fit_zip_stored_as_received(self, tmp_path):
        raw = b"PK\x03\x04zip-bytes"
        meta = CaptureMeta(
            ext="zip",
            capture_mode=MODE_RAW,
            request_url="download_activity",
            request_params={"activity_id": 1, "format": "ORIGINAL"},
            content_type="application/zip",
            charset=None,
        )
        payload = capture_bronze(
            "garmin", "activity_fit", raw, meta, bronze_root=str(tmp_path),
        )
        assert Path(payload).suffix == ".zip"
        assert Path(payload).read_bytes() == raw
        sidecar = json.loads(Path(payload + ".meta.json").read_text())
        assert sidecar["capture_mode"] == MODE_RAW
        assert sidecar["charset"] is None
        assert sidecar["content_type"] == "application/zip"


class TestNonFatal:
    def test_capture_failure_is_swallowed(self, tmp_path, monkeypatch):
        # Force the atomic write to blow up; capture must return None, not raise.
        import garmincapture.bronze as bronze_mod

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(bronze_mod, "_atomic_write", boom)
        result = capture_bronze(
            "garmin", "sleep", b"{}", _json_meta(), bronze_root=str(tmp_path),
        )
        assert result is None


class TestPairAtomicity:
    def test_payload_write_failure_leaves_no_orphan_sidecar(self, tmp_path, monkeypatch):
        """If the payload write fails, the already-written sidecar is removed so a
        reader never sees a sidecar pointing at a missing payload."""
        import garmincapture.bronze as bronze_mod

        real_write = bronze_mod._atomic_write
        calls = {"n": 0}

        def fail_on_payload(target, data):
            # First call is the sidecar (write it for real); second is the payload.
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full on payload")
            return real_write(target, data)

        monkeypatch.setattr(bronze_mod, "_atomic_write", fail_on_payload)
        result = capture_bronze(
            "garmin", "sleep", b'{"x":1}', _json_meta(), bronze_root=str(tmp_path),
        )
        assert result is None
        # No payload AND no orphaned sidecar left behind.
        leftovers = list((tmp_path).rglob("*"))
        files = [p for p in leftovers if p.is_file()]
        assert files == [], f"unexpected leftover files: {files}"
