"""Bronze write contract — ``capture_bronze`` and friends.

Implements the Bronze Layer Specification verbatim for this puller:

* ``source = "garmin"`` for every collection.
* **Disabled by default**: if ``bronze_root`` is empty, ``capture_bronze`` is a
  complete noop — a single early guard, no path computation, no dir creation,
  no per-call warnings.
* **Bytes only**: callers pass either ``download_*`` bytes (``raw`` grade) or the
  deterministic ``json.dumps(...).encode("utf-8")`` of a parsed return
  (``reserialized`` grade). Never ``response.text``.
* **Append-only / immutable / unique filename** — names embed a millisecond
  timestamp plus a short random id and are never reused.
* **Path standard**:
  ``{bronze_root}/garmin/{collection}/dt={YYYY-MM-DD}/{collection}_{unix_ms}_{short_id}.{ext}``
  where ``dt`` is the **UTC fetch date**, not the event date.
* **Sidecar ``.meta.json``** written alongside every payload.
* **Atomic writes**: temp file + ``os.replace`` for both payload and sidecar.
* **Non-fatal**: any failure is logged as a warning and swallowed — capture
  never stops the pull.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .logging_config import get_logger

log = get_logger(__name__)

SOURCE = "garmin"
PROCESSOR = "garmincapture"
SCHEMA_VERSION = "v1"

# Capture provenance grades (see the spec, §6).
MODE_RAW = "raw"
MODE_RESERIALIZED = "reserialized"


@dataclass(slots=True)
class CaptureMeta:
    """Grade-specific metadata supplied by the caller for a single capture.

    The remaining sidecar fields (timestamps, hashes, sizes, processor stamps)
    are computed inside ``capture_bronze`` from the bytes actually written.
    """

    ext: str
    capture_mode: str
    request_url: str
    request_params: dict
    content_type: str | None = "application/json"
    charset: str | None = "utf-8"
    content_encoding: str = "identity"
    stored_encoding: str = "identity"
    http_status: int = 200
    redacted_fields: list[str] = field(default_factory=list)


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically via a temp file + ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_", suffix=target.suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # Best effort cleanup of the temp file on any failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def capture_bronze(
    source: str,
    collection: str,
    raw_bytes: bytes,
    meta: CaptureMeta,
    *,
    bronze_root: str | None,
    processor_version: str = "dev",
) -> str | None:
    """Capture ``raw_bytes`` into the bronze layer. Returns the payload path or None.

    ``bronze_root`` empty/None => complete noop (returns None immediately, no
    logging, no path computation). Any failure after that is logged and
    swallowed so capture is never fatal to the pull.
    """
    # Single early guard — disabled by default.
    if not bronze_root or not str(bronze_root).strip():
        return None

    try:
        now = datetime.now(UTC)
        unix_ms = int(now.timestamp() * 1000)
        fetched_at = now.isoformat()
        dt = now.strftime("%Y-%m-%d")  # UTC fetch date, not event date
        short_id = uuid.uuid4().hex[:6]

        byte_size = len(raw_bytes)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        base = f"{collection}_{unix_ms}_{short_id}"
        out_dir = Path(bronze_root) / source / collection / f"dt={dt}"
        payload_path = out_dir / f"{base}.{meta.ext}"
        sidecar_path = out_dir / f"{base}.{meta.ext}.meta.json"

        sidecar = {
            "source": source,
            "collection": collection,
            "fetched_at": fetched_at,
            "fetched_at_unix_ms": unix_ms,
            "request_url": meta.request_url,
            "request_params": meta.request_params,
            "http_status": meta.http_status,
            "content_type": meta.content_type,
            "charset": meta.charset,
            "content_encoding": meta.content_encoding,
            "stored_encoding": meta.stored_encoding,
            "capture_mode": meta.capture_mode,
            "byte_size": byte_size,
            "sha256": sha256,
            "redacted_fields": meta.redacted_fields,
            "processor": PROCESSOR,
            "processor_version": processor_version,
            "schema_version": SCHEMA_VERSION,
        }
        sidecar_bytes = json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8")

        # Each file is atomic, but the *pair* is not. We make the pair
        # best-effort-atomic so we never leave an unprovenanced payload: write the
        # sidecar first (a stray sidecar with no payload is harmless), then the
        # payload. If the payload write fails, drop the orphaned sidecar so a
        # sidecar-driven reader never points at a missing file.
        _atomic_write(sidecar_path, sidecar_bytes)
        try:
            _atomic_write(payload_path, raw_bytes)
        except BaseException:
            try:
                sidecar_path.unlink()
            except OSError:
                pass
            raise

        log.debug(
            "bronze_captured",
            source=source,
            collection=collection,
            path=str(payload_path),
            capture_mode=meta.capture_mode,
            byte_size=byte_size,
        )
        return str(payload_path)
    except Exception as exc:  # noqa: BLE001 — capture must never be fatal
        log.warning(
            "bronze_capture_failed",
            source=source,
            collection=collection,
            error=str(exc),
        )
        return None
