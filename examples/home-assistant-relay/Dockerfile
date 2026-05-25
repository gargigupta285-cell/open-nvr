# syntax=docker/dockerfile:1.7
# home-assistant-relay example app
#
# Build (from the open-nvr repo root):
#   docker build -f examples/home-assistant-relay/Dockerfile -t opennvr/home-assistant-relay:1.0.0 .
#
# Run (talks to NATS + MQTT broker / HA REST on the host network):
#   docker run --rm \
#     --network host \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     opennvr/home-assistant-relay:1.0.0 \
#     --config /app/config.yml

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
        "httpx>=0.27,<1.0" \
        "PyYAML>=6.0,<7.0" \
        "nats-py>=2.6,<3.0" \
        "paho-mqtt>=2.0,<3.0"

COPY examples/home-assistant-relay/__init__.py            ./__init__.py
COPY examples/home-assistant-relay/ha_mapper.py           ./ha_mapper.py
COPY examples/home-assistant-relay/publishers.py          ./publishers.py
COPY examples/home-assistant-relay/home_assistant_relay.py ./home_assistant_relay.py
COPY examples/home-assistant-relay/config.example.yml     ./config.example.yml

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — the relay is a NATS subscriber, no HTTP surface.

ENTRYPOINT ["python", "home_assistant_relay.py"]
CMD ["--config", "config.yml"]
