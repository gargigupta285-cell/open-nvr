# syntax=docker/dockerfile:1.7
# package-delivery example app
#
# Build (from the open-nvr repo root):
#   docker build -f examples/package-delivery/Dockerfile -t opennvr/package-delivery:1.0.0 .
#
# Run (talks to KAI-C + YOLOv8 on the host network):
#   docker run --rm \
#     --network host \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     opennvr/package-delivery:1.0.0 \
#     --config /app/config.yml

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pillow is used to read JPEG dimensions for ROI math; it's a tiny dep
# but pulled here explicitly so the image build is reproducible.
RUN pip install --no-cache-dir \
        "httpx>=0.27,<1.0" \
        "PyYAML>=6.0,<7.0" \
        "nats-py>=2.6" \
        "Pillow>=10.0"

COPY examples/package-delivery/__init__.py        ./__init__.py
COPY examples/package-delivery/alerts.py          ./alerts.py
COPY examples/package-delivery/frame_sources.py   ./frame_sources.py
COPY examples/package-delivery/package_pipeline.py ./package_pipeline.py
COPY examples/package-delivery/package_delivery.py ./package_delivery.py
COPY examples/package-delivery/config.example.yml ./config.example.yml

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — the daemon loop never exposes an HTTP surface.
# Docker's "process is alive" liveness is what matters here.

ENTRYPOINT ["python", "package_delivery.py"]
CMD ["--config", "config.yml"]
