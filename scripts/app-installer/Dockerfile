# syntax=docker/dockerfile:1.7
# OpenNVR App Installer — the single privileged reconciler.
#
# This is the ONLY OpenNVR container that gets the Docker socket mounted
# (see docker-compose.installer.yml, profile: app-installer). It is NOT
# network-facing: it opens no listening ports and only talks to the DB
# (to read desired state) and the Docker socket (to apply it).
#
# It ships the Docker CLI + compose plugin so it can shell out to
# ``docker compose`` against the host daemon over the mounted socket.
#
# Build (from the repo root):
#   docker build -f scripts/app-installer/Dockerfile -t opennvr/app-installer:local .
#
# Opt in via the overlay (see docs/APPS_INSTALL.md):
#   docker compose -f docker-compose.yml -f docker-compose.installer.yml \
#       --profile app-installer up -d app-installer

FROM python:3.11-slim AS runtime

# Runs as root (the base image default — no USER directive). This is
# deliberate and required: the container talks to the host daemon over
# the bind-mounted /var/run/docker.sock, whose group ownership varies by
# host, so it needs root to open the socket reliably. This is the SINGLE
# privileged component in the stack, it is opt-in (only started via the
# app-installer overlay), and it is NON-network-facing (no EXPOSE, no
# listening ports) — its only reachable surfaces are the DB and the
# socket. See docs/APPS_INSTALL.md → "The single privileged mount".

# Docker CLI + compose plugin only (NOT the daemon) — the reconciler
# drives the HOST daemon through the mounted /var/run/docker.sock.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        docker.io \
        docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# SQLAlchemy is the only Python dep — kept minimal on purpose.
RUN pip install --no-cache-dir "sqlalchemy>=2,<3" "psycopg2-binary>=2.9"

COPY scripts/app-installer/reconciler.py ./reconciler.py
COPY scripts/app-installer/store.py      ./store.py
COPY scripts/app-installer/main.py       ./main.py

ENV PYTHONUNBUFFERED=1

# No EXPOSE — the installer is never network-facing.

ENTRYPOINT ["python", "main.py"]
