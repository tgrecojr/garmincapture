"""Deterministic JSON serialization and secret-screening helpers.

Two responsibilities, both small and pure so they are trivially testable:

1. ``to_bronze_json`` — turn a parsed JSON object (dict/list/scalar) back into
   bytes for the ``reserialized`` capture grade. Deterministic, compact, no
   ``sort_keys`` (we preserve the library's key order), UTF-8.

2. ``screen_for_secrets`` — the *one* permitted bronze payload modification.
   Applied only to endpoints flagged for it (profile/settings), it recursively
   removes any key whose name matches a secret pattern and returns the list of
   redacted key paths for the sidecar's ``redacted_fields``.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Keys whose *name* indicates a credential/secret. Matched case-insensitively
# as a substring of the key. Deliberately broad: bronze is immutable and
# replicated, so over-redaction is cheap and under-redaction is forbidden.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "apikey",
    "api_key",
    "authorization",
    "auth_token",
    "authtoken",
    "credential",
    "private_key",
    "privatekey",
    "client_secret",
    "clientsecret",
    "signature",
    "sessionid",
    "session_id",
)

_SECRET_KEY_RE = re.compile("|".join(re.escape(p) for p in _SECRET_KEY_PATTERNS), re.IGNORECASE)


def to_bronze_json(parsed: Any) -> bytes:
    """Serialize a parsed JSON object to deterministic bronze bytes.

    Compact separators, ``ensure_ascii=False`` (so UTF-8 text stays UTF-8),
    and *no* ``sort_keys`` — reordering keys would be a reshaping we explicitly
    do not want. The resulting bytes are exactly what is written to disk and
    what ``byte_size``/``sha256`` are computed over.
    """
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _key_is_secret(key: Any) -> bool:
    return isinstance(key, str) and bool(_SECRET_KEY_RE.search(key))


def screen_for_secrets(obj: Any, *, path: str = "") -> tuple[Any, list[str]]:
    """Recursively remove secret-looking keys from a parsed JSON object.

    Returns a ``(screened_object, redacted_field_paths)`` tuple. The input is
    not mutated. ``redacted_field_paths`` uses dotted/indexed paths
    (e.g. ``profile.accessToken``) so the sidecar records exactly what was
    dropped.

    This is the only payload modification the bronze spec permits, and the
    runner only invokes it for endpoints explicitly flagged ``screen_secrets``.
    """
    redacted: list[str] = []

    def _walk(node: Any, node_path: str) -> Any:
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for key, value in node.items():
                child_path = f"{node_path}.{key}" if node_path else str(key)
                if _key_is_secret(key):
                    redacted.append(child_path)
                    continue
                out[key] = _walk(value, child_path)
            return out
        if isinstance(node, list):
            return [
                _walk(item, f"{node_path}[{idx}]")
                for idx, item in enumerate(node)
            ]
        return node

    screened = _walk(obj, path)
    return screened, redacted
