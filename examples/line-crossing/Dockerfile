# syntax=docker/dockerfile:1.7
# line-crossing example app
#
# Build (from the open-nvr repo root — the context must include
# sdk/opennvr-app-sdk, which this app depends on):
#   docker build -f examples/line-crossing/Dockerfile -t opennvr/line-crossing:1.0.0 .
#
# Run (subscribes to the NATS event bus on the compose network):
#   docker run --rm \
#     --network opennvr_internal \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     opennvr/line-crossing:1.0.0 \
#     --config /app/config.yml
#
# Or use the repo-root docker-compose.apps.yml overlay, which builds
# this image, generates the config from config.docker.yml, and wires
# NATS automatically.

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The SDK owns the runtime stack (nats-py subscribe loop, httpx alert
# webhooks, PyYAML config) — install it from the repo checkout so the
# image always matches the in-tree contract surface.
COPY sdk/opennvr-app-sdk /opt/opennvr-app-sdk
RUN pip install --no-cache-dir /opt/opennvr-app-sdk

COPY examples/line-crossing/line_crossing.py     ./line_crossing.py
COPY examples/line-crossing/alerts.py            ./alerts.py
COPY examples/line-crossing/line.py              ./line.py
COPY examples/line-crossing/config.example.yml   ./config.example.yml

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — liveness is Docker's "process is alive"; this app's
# AppConfig has no contract_port field, so no contract server runs.

ENTRYPOINT ["python", "line_crossing.py"]
CMD ["--config", "config.yml"]
