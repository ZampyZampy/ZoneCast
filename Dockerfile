# Python 3.11 is pinned deliberately: the `audioop` stdlib module used for
# G.711 encoding of the RTP paging stream was removed in Python 3.13.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

# Security hardening: run as an unprivileged user rather than root.
# The one feature that needs a real privilege (manual clock set, see
# app/services/system_time.py) gets it via a FILE capability on the
# `date` binary alone, not by running the whole process as root — this
# still requires `cap_add: SYS_TIME` in docker-compose.yml (a
# capability only takes effect for a non-root process if it's both in
# the container's bounding set AND attached to the binary it execs).
RUN setcap cap_sys_time+ep /bin/date
# Lets the app bind privileged ports (e.g. 80) without running as root —
# see APP_PORT in .env / config.py. Granting it to the interpreter binary
# itself (rather than a wrapper) is broad, but this image runs nothing
# else with that interpreter, so the practical exposure is the same as
# scoping it to the app.
RUN setcap cap_net_bind_service+ep $(readlink -f $(which python3))
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY migrations ./migrations

RUN mkdir -p /app/media /app/data /app/backups && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Shell form (not exec form) so ${APP_PORT} is actually expanded — set it
# in .env to change the listening port (default 8000 if unset).
CMD uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8000}
