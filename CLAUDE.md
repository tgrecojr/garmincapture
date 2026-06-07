# garmincapture

## Overview

A thin, standalone Garmin Connect raw-capture service. It authenticates once
(auth fully delegated to the `garminconnect` library), pulls the entire
available data surface, and lands it into the shared **bronze layer** with **no
transformation**. It owns only the *capture* concern and sits beside
`garmin-grafana`. Silver/transformation is explicitly out of scope.

## Tech Stack

- Language: Python (>= 3.14)
- Key deps: `garminconnect` (track latest, `>=0.3.5`, never pin to `==0.3.3`),
  `pydantic` / `pydantic-settings`, `structlog`
- Tooling: `uv` (deps + lock), `pytest` (+cov, +mock)
- Packaging: multi-stage Chainguard Docker image, `nonroot`, multi-arch

## Commands

- `uv sync` — install dependencies (add `--frozen` in CI)
- `uv run python -m garmincapture` — always-on incremental loop (pull, sleep `POLL_INTERVAL_SECONDS`, repeat)
- `uv run python -m garmincapture --once` — single incremental pull, then exit (cron/testing)
- `uv run python -m garmincapture --start YYYY-MM-DD --end YYYY-MM-DD` — one-off backfill (no loop; overrides `LOOKBACK_DAYS`)
- `uv run pytest tests/ -v --tb=short` — run tests
- `docker compose run --rm bootstrap` — one-time interactive MFA token bootstrap
- `docker compose up garmincapture` — unattended incremental run

## Architecture

```
garmincapture/
  config.py     # pydantic-settings env parsing; BRONZE_ROOT, window, toggles
  auth.py       # thin wrapper around garminconnect login + token store (delegated)
  bronze.py     # capture_bronze(): path/sidecar/atomic/non-fatal; noop when disabled
  serialize.py  # deterministic json.dumps + secret-screening
  catalog.py    # allowlisted endpoint registry (Buckets A/B/C) + drift detector
  runner.py     # orchestration: window iteration, fan-out, rate limiting
  __main__.py   # entrypoint (incremental / backfill)
  logging_config.py  # structlog (json|console)
```

Flow: `runner.run(window)` → Bucket C reference/metadata (incl. per-device and
per-profile fan-out) → Bucket A daily/range/weekly metrics across the window →
Bucket B activities list + per-activity fan-out (JSON detail at `reserialized`,
FIT/original at `raw`).

### Non-negotiable invariants

- **Allowlist only.** Only data endpoints appear in `catalog.py`. Mutating/auth/
  plumbing methods (`add_*`, `set_*`, `create_*`, `delete_*`, `upload_*`,
  `login`, `logout`, `request_reload`, …) are NEVER imported or called. A unit
  test enforces this. New endpoints are surfaced by `detect_catalog_drift`, not
  auto-called.
- **Two capture grades.** `raw` bytes for `download_*` (FIT stored as-received
  `.zip`, never unzipped); `reserialized` for all JSON via
  `json.dumps(parsed, ensure_ascii=False, separators=(",",":"))` — never
  `response.text`, never `sort_keys`. Record `capture_mode` in every sidecar.
- **`BRONZE_ROOT` unset ⇒ complete noop** (single early guard in `capture_bronze`).
- **Capture is non-fatal.** Wrap writes in try/except, log a warning, never
  re-raise. The pull halts only on auth / 429 errors.
- **No secret in bronze.** Token store lives on its own volume, never under
  `BRONZE_ROOT`. Profile/settings payloads are secret-screened; removals recorded
  in `redacted_fields`. Prefer not capturing over risking a secret.
- **Append-only / immutable / unique filenames.** Never reuse a name; never dedup
  at this layer.

## Environment Variables

See `.env.example`. Required for a real pull: `BRONZE_ROOT`, `GARMINTOKENS`,
`GARMINCONNECT_EMAIL`, `GARMINCONNECT_BASE64_PASSWORD`. Optional: `LOOKBACK_DAYS`,
`WEEKLY_WEEKS`, `POLL_INTERVAL_SECONDS`, `RATE_LIMIT_SECONDS`, `FETCH_SELECTION`,
`FETCH_EXCLUDE`, `CAPTURE_ALT_FORMATS`, `GARMINCONNECT_IS_CN`, `PROCESSOR_VERSION`,
`LOG_LEVEL`, `LOG_FORMAT`.
(Values are never committed; `.env` is gitignored.)

`FETCH_SELECTION` is an allowlist (empty ⇒ everything); `FETCH_EXCLUDE` is a
denylist that **wins over** selection. `FETCH_EXCLUDE` defaults to the
female-health endpoints (`menstrual_day,menstrual_calendar,pregnancy_summary`) so
they are never called; set `FETCH_EXCLUDE=""` to capture them. These endpoints
remain in `catalog.py` (so drift detection still tracks them) — they are simply
not invoked.

Empty (`[]`/`{}`) returns are captured as-is for endpoints without a skip flag
(e.g. `hrv`, `training_readiness`, `body_battery_events`, `running_tolerance`,
`goals`): an empty 200 is a faithful "asked, no data" record, and these begin
populating automatically if a future device produces them. See
`scripts/probe-empty-endpoints.py` to verify live what a date returns.
