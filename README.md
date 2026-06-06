# garmincapture

A thin Garmin Connect raw-capture service that authenticates once, pulls the
entire available data surface (daily wellness metrics, activity summaries +
details, the raw FIT/original files, training/biometric metrics, reference
metadata), and lands it into the shared **bronze layer** with no transformation.

This is a best-effort personal archive that sits beside `garmin-grafana` — it
owns only the *capture* concern. Silver/transformation is explicitly out of
scope.

## How it works

- **Auth is fully delegated** to the [`garminconnect`](https://pypi.org/project/garminconnect/)
  library (Cloudflare challenges, MFA, token refresh, CN domains). We track the
  latest version and never pin to an old one.
- **Allowlisted catalog.** The pull is driven from a declarative registry of
  *data* endpoints (`catalog.py`). Mutating/auth/plumbing methods are absent, so
  they can never be called by accident. A **drift detector** introspects the
  library on every run and loudly logs any new readable endpoint not yet in the
  catalog — so a library upgrade's "free" data is surfaced for review, never
  silently missed or silently called.
- **Two capture grades** (recorded per object as `capture_mode`):
  - `raw` — true bytes, stored untouched. The FIT/original activity files (the
    one irreplaceable artifact) are stored as-received `.zip`, never unzipped.
  - `reserialized` — the parsed JSON the library returns, written back out via a
    deterministic `json.dumps(..., ensure_ascii=False, separators=(",",":"))`
    (no `sort_keys`). A small, documented round-trip loss we accept because
    every downstream reader parses JSON anyway.
- **Capture is best-effort and non-fatal.** A bronze write failure never stops
  the pull. The run halts only on auth or rate-limit (429) errors.
- **`BRONZE_ROOT` unset ⇒ complete noop.** The puller runs but writes nothing.

## Bronze layout

```
{BRONZE_ROOT}/garmin/{collection}/dt={YYYY-MM-DD}/{collection}_{unix_ms}_{short_id}.{ext}
```

`dt` is the **UTC fetch date** (not the event date). Every payload has a sidecar
`*.meta.json` recording `source`, `collection`, timestamps, the logical
`request_url`/`request_params` (no secrets), `capture_mode`, `byte_size`,
`sha256` (of the stored bytes), `redacted_fields`, and processor stamps.

## Configuration

All configuration is via environment variables — see `.env.example` for the full
list. The essentials:

| Var | Meaning | Default |
|---|---|---|
| `BRONZE_ROOT` | Bronze base dir; unset/empty = capture disabled | *(unset → noop)* |
| `GARMINTOKENS` | Token store dir (mounted volume) | `/tokens` |
| `GARMINCONNECT_EMAIL` | Login email (first-run / credential mode) | — |
| `GARMINCONNECT_BASE64_PASSWORD` | Base64 password | — |
| `GARMINCONNECT_IS_CN` | China domain | `false` |
| `LOOKBACK_DAYS` | Incremental trailing window (days) | `7` |
| `WEEKLY_WEEKS` | Trailing weeks for weekly-aggregate endpoints | `4` |
| `POLL_INTERVAL_SECONDS` | Sleep between incremental pulls (always-on loop) | `900` (15 min) |
| `RATE_LIMIT_SECONDS` | Inter-call sleep | `2` |
| `FETCH_SELECTION` | Optional subset of catalog names; empty = all | *(all)* |
| `CAPTURE_ALT_FORMATS` | Also pull TCX/GPX/KML/CSV per activity | `false` |
| `PROCESSOR_VERSION` | Stamped into sidecars | `dev` (git SHA in CI) |

## Running

### Local (development)

```bash
uv sync                                  # install deps (incl. dev)
cp .env.example .env                      # then edit .env
uv run python -m garmincapture            # always-on loop: pull, sleep POLL_INTERVAL_SECONDS, repeat
uv run python -m garmincapture --once     # a single incremental pull, then exit
uv run python -m garmincapture --start 2025-01-01 --end 2025-12-31   # one-off backfill
```

### Execution modes

- **Incremental (default):** an always-on loop. Each cycle pulls the trailing
  `LOOKBACK_DAYS` window, then sleeps `POLL_INTERVAL_SECONDS` (default 15 min).
  Garmin re-scores recent days, so re-pulling a small trailing window each cycle
  is correct — bronze is append-only, so every restatement lands as a new file.
  SIGTERM/SIGINT (`docker stop`, Ctrl-C) shuts the loop down cleanly between
  cycles. A 429 backs off until the next cycle; an auth error triggers a silent
  re-login.
- **`--once`:** a single incremental pull, then exit — for cron/systemd setups
  or testing.
- **Backfill (`--start/--end`):** a one-off, reverse-chronological pull that
  never loops and **overrides `LOOKBACK_DAYS`**. Newest-first, so an interrupted
  run leaves the most recent data captured. (`WEEKLY_WEEKS` is *not* widened by
  the date range — bump it for the invocation if you want deeper weekly history.)

Garmin cold-storage reality: intraday detail older than ~6 months is gone from
the API; daily aggregates remain. Thin/empty bodies are captured as-is.

### Docker

```bash
docker compose build

# One-time interactive token bootstrap (MFA). Capture is disabled here; this
# only mints the token store under ./data/tokens.
docker compose run --rm bootstrap

# Always-on incremental puller (loops on POLL_INTERVAL_SECONDS, restarts on reboot):
docker compose up -d garmincapture

# Ad-hoc backfill against the running container (token store already minted):
docker compose exec garmincapture python -m garmincapture --start 2025-01-01 --end 2025-12-31
```

The runtime image is Chainguard `python:latest` — shell-less and near-zero-CVE.
Debug locally with the `:latest-dev` variant; never shell into prod.

> **First-run MFA** is interactive and cannot be hosted by the shell-less runtime
> as a normal TTY prompt. Use `docker compose run --rm bootstrap` (stdin wired)
> for the one-time bootstrap; the persisted token store under `./data/tokens`
> then carries all subsequent unattended runs.

## Testing

```bash
uv run pytest tests/ -v --tb=short
```

Tests cover the catalog allowlist (no Bucket-D methods present) and drift
detector, the secret-screening function, the deterministic serializer, the
`BRONZE_ROOT`-unset noop, and a mocked end-to-end capture asserting file paths
and sidecar fields.

## CI/CD

Four workflows mirror the rest of the fleet (`glucose-loader`):

- `test.yml` — pytest on every PR.
- `supply-chain.yml` — Socket Security + OSV-scanner + pip-audit on the locked
  dependency set (reusable, `workflow_call`).
- `docker-publish.yml` — on push to `main`: runs supply-chain first, builds
  multi-arch (amd64/arm64), pushes to GHCR, cosign keyless-signs, generates an
  SBOM, and attests SBOM + provenance (fail-soft on user-owned repos).
- `ghcr-retention.yml` — weekly prune keeping `latest` + 5 most-recent tagged.

## Security

- No token/credential/secret is ever written under `BRONZE_ROOT`. The token
  store lives on its own mounted volume, written by the library.
- `get_user_profile` / `get_userprofile_settings` payloads are screened for
  secret-looking keys before capture; any removal is recorded in the sidecar's
  `redacted_fields` (the one permitted payload modification).
- No HTTP session tap, so there is no auth-traffic capture risk by construction.
