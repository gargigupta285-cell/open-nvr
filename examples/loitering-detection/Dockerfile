# syntax=docker/dockerfile:1.7
# loitering-detection example app
#
# Build (from the open-nvr repo root — the context must include
# sdk/opennvr-app-sdk, which this app depends on):
#   docker build -f examples/loitering-detection/Dockerfile -t opennvr/loitering-detection:1.0.0 .
#
# Run (subscribes to the NATS event bus on the compose network):
#   docker run --rm \
#     --network opennvr_internal \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     opennvr/loitering-detection:1.0.0 \
#     --config /app/config.yml
#
# Or use the repo-root docker-compose.apps.yml overlay, which builds
# this image, generates the config from config.docker.yml, and wires
# NATS + registry self-registration automatically.

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

COPY examples/loitering-detection/loitering_detection.py ./loitering_detection.py
COPY examples/loitering-detection/alerts.py              ./alerts.py
COPY examples/loitering-detection/zone.py                ./zone.py
COPY examples/loitering-detection/config.example.yml     ./config.example.yml

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — liveness is Docker's "process is alive"; readiness
# is the SDK contract server (/health on contract_port when enabled).

ENTRYPOINT ["python", "loitering_detection.py"]
CMD ["--config", "config.yml"]
