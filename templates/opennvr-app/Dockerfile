# syntax=docker/dockerfile:1.7
# __APP_NAME__ — OpenNVR Detector app
#
# Build (from the open-nvr repo root — the context must include
# sdk/opennvr-app-sdk, which this app depends on):
#   docker build -f examples/__APP_ID__/Dockerfile -t opennvr/__APP_ID__:0.1.0 .
#
# Run (subscribes to the NATS event bus on the compose network):
#   docker run --rm \
#     --network opennvr_internal \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     opennvr/__APP_ID__:0.1.0 \
#     --config /app/config.yml
#
# Or wire it into the repo-root docker-compose.apps.yml overlay (see
# docs/CONTRIBUTING_APPS.md for the App Store install path).
#
# NOTE: the COPY paths below assume the app lives at examples/__APP_ID__/
# inside the open-nvr repo (the generator's default --dest). If you
# scaffolded OUT of tree, adjust the COPY sources and vendor or pip-install
# opennvr-app-sdk yourself — this file won't build as-is from elsewhere.

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The SDK owns the runtime stack — install it from the repo checkout so
# the image always matches the in-tree contract surface.
COPY sdk/opennvr-app-sdk /opt/opennvr-app-sdk
RUN pip install --no-cache-dir /opt/opennvr-app-sdk

COPY examples/__APP_ID__/__APP_MODULE__.py    ./__APP_MODULE__.py
COPY examples/__APP_ID__/config.example.yml   ./config.example.yml

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — liveness is Docker's "process is alive"; readiness is
# the SDK contract server (/health on contract_port when enabled).

ENTRYPOINT ["python", "__APP_MODULE__.py"]
CMD ["--config", "config.yml"]
