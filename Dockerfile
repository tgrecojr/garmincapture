# syntax=docker/dockerfile:1.24@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
FROM cgr.dev/chainguard/python:latest-dev@sha256:c1d503ebc5088bd0143673af0d02f2db31e53acc506ba5a8f4756c337a989d3f AS builder

USER root
WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
 && mkdir -p /app/data


FROM cgr.dev/chainguard/python:latest@sha256:f960fea6d1fb1c0ad626558d9db323ff84468927ac37cd7fa889b512ba0dc1c9

WORKDIR /app

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv
COPY --from=builder --chown=nonroot:nonroot /app/data /app/data
COPY --chown=nonroot:nonroot garmincapture/ ./garmincapture/

ENV PATH="/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/bin:/usr/sbin:/sbin:/bin" \
    PYTHONUNBUFFERED=1

# /app/data holds both BRONZE_ROOT and the garminconnect token store as mounted subdirs.
VOLUME ["/app/data"]

USER nonroot

ENTRYPOINT ["/app/.venv/bin/python"]
CMD ["-m", "garmincapture"]
