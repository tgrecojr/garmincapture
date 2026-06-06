"""Tests for the deterministic serializer and secret screening."""

from __future__ import annotations

import json

from garmincapture.serialize import screen_for_secrets, to_bronze_json


class TestToBronzeJson:
    def test_compact_separators_no_spaces(self):
        assert to_bronze_json({"a": 1, "b": 2}) == b'{"a":1,"b":2}'

    def test_preserves_key_order_no_sort(self):
        # Insertion order, NOT alphabetical (no sort_keys).
        assert to_bronze_json({"z": 1, "a": 2}) == b'{"z":1,"a":2}'

    def test_unicode_preserved_not_escaped(self):
        assert to_bronze_json({"name": "café"}) == '{"name":"café"}'.encode("utf-8")

    def test_roundtrips_to_equivalent_object(self):
        obj = {"list": [1, 2, {"nested": True}], "n": None}
        assert json.loads(to_bronze_json(obj)) == obj


class TestScreenForSecrets:
    def test_removes_token_like_keys(self):
        obj = {"displayName": "x", "accessToken": "abc", "refreshToken": "def"}
        screened, redacted = screen_for_secrets(obj)
        assert screened == {"displayName": "x"}
        assert set(redacted) == {"accessToken", "refreshToken"}

    def test_nested_redaction_with_paths(self):
        obj = {"profile": {"id": 1, "password": "p"}, "items": [{"apiKey": "k"}]}
        screened, redacted = screen_for_secrets(obj)
        assert screened == {"profile": {"id": 1}, "items": [{}]}
        assert "profile.password" in redacted
        assert "items[0].apiKey" in redacted

    def test_no_secrets_leaves_object_untouched(self):
        obj = {"weight": 80, "height": 180, "nested": {"vo2max": 50}}
        screened, redacted = screen_for_secrets(obj)
        assert screened == obj
        assert redacted == []

    def test_does_not_mutate_input(self):
        obj = {"token": "x", "keep": 1}
        screen_for_secrets(obj)
        assert obj == {"token": "x", "keep": 1}

    def test_case_insensitive_matching(self):
        obj = {"AUTHORIZATION": "Bearer x", "Secret": "y"}
        screened, redacted = screen_for_secrets(obj)
        assert screened == {}
        assert len(redacted) == 2
