# syntax=docker/dockerfile:1.7
# footage-search example app
#
# Build (from the open-nvr repo root — the context must include
# sdk/opennvr-app-sdk, which this app depends on):
#   docker build -f examples/footage-search/Dockerfile -t opennvr/footage-search:1.0.0 .
#
# Run the indexer daemon (subscribes to the NATS event bus on the
# compose network; the SQLite index lands in /app/data — mount a
# volume there so it survives restarts):
#   docker run --rm \
#     --network opennvr_internal \
#     -v $(pwd)/config.yml:/app/config.yml:ro \
#     -v opennvr_footage_search_data:/app/data \
#     opennvr/footage-search:1.0.0 \
#     --config /app/config.yml index
#
# Search the index from a running container:
#   docker compose -f docker-compose.yml -f docker-compose.apps.yml \
#     exec footage-search \
#     python footage_search.py --config /app/config/config.yml \
#     search "red truck at the dock yesterday"
#
# Or use the repo-root docker-compose.apps.yml overlay, which builds
# this image, generates the config from config.docker.yml, and wires
# NATS + the data volume automatically.

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The SDK owns the entire runtime stack (nats-py subscribe loop,
# PyYAML config, httpx — which the optional Ollama query parser also
# rides). SQLite is in the stdlib. Install the SDK from the repo
# checkout so the image always matches the in-tree contract surface.
COPY sdk/opennvr-app-sdk /opt/opennvr-app-sdk
RUN pip install --no-cache-dir /opt/opennvr-app-sdk

# The entry module's full import graph: query.py (NL → structured
# filter parsers) + store.py (the SQLite keyframe index). A missed
# module here is a startup crash.
COPY examples/footage-search/footage_search.py ./footage_search.py
COPY examples/footage-search/query.py          ./query.py
COPY examples/footage-search/store.py          ./store.py
COPY examples/footage-search/config.example.yml ./config.example.yml

# The SQLite index lives here (config.docker.yml points db_path at
# /app/data/footage_index.sqlite3); the compose overlay mounts a named
# volume so the index outlives the container.
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

# No HEALTHCHECK — liveness is Docker's "process is alive". (This
# app's config surface has no contract_port key, so the SDK contract
# server is not started; see load_config in footage_search.py.)

ENTRYPOINT ["python", "footage_search.py"]
CMD ["--config", "config.yml", "index"]
