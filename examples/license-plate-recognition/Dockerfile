# syntax=docker/dockerfile:1.7
# license-plate-recognition example app
#
# Build (from the open-nvr repo root — the context must include
# sdk/opennvr-app-sdk, which this app depends on):
#   docker build -f examples/license-plate-recognition/Dockerfile -t opennvr/license-plate-recognition:1.0.0 .
#
# Run (polls camera snapshot URLs and drives YOLOv8 + fast-plate-ocr
# through KAI-C on the compose network):
#   docker run --rm \
#     --network opennvr_internal \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     opennvr/license-plate-recognition:1.0.0 \
#     --config /app/config.yml
#
# Or use the repo-root docker-compose.apps.yml overlay, which builds
# this image, generates the config from config.docker.yml, and wires
# KAI-C + registry self-registration automatically.

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pillow stays app-side (the SDK deliberately doesn't own it): cropping
# the vehicle bbox before the OCR call is this app's pipeline concern.
# Pin matches pyproject.toml. Manylinux wheels exist for 3.11-slim, so
# no retry scaffolding is needed (that's reserved for torch-scale pulls
# — see examples/camera-agent/Dockerfile).
RUN pip install --no-cache-dir "Pillow>=10.0,<12.0"

# The SDK owns the rest of the runtime stack (httpx KAI-C calls +
# webhooks, PyYAML config, nats-py alert fan-out, the FrameApp poll
# loop and frame sources) — install it from the repo checkout so the
# image always matches the in-tree contract surface.
COPY sdk/opennvr-app-sdk /opt/opennvr-app-sdk
RUN pip install --no-cache-dir /opt/opennvr-app-sdk

# The entry module's full import graph: alerts.py + frame_sources.py
# are the SDK compatibility shims, plate_pipeline.py is the two-stage
# detect → crop → OCR chain. A missed module here is a startup crash.
COPY examples/license-plate-recognition/license_plate_recognition.py ./license_plate_recognition.py
COPY examples/license-plate-recognition/plate_pipeline.py            ./plate_pipeline.py
COPY examples/license-plate-recognition/alerts.py                    ./alerts.py
COPY examples/license-plate-recognition/frame_sources.py             ./frame_sources.py
COPY examples/license-plate-recognition/config.example.yml           ./config.example.yml

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — liveness is Docker's "process is alive"; readiness
# is the SDK contract server (/health on contract_port when enabled).

ENTRYPOINT ["python", "license_plate_recognition.py"]
CMD ["--config", "config.yml"]
