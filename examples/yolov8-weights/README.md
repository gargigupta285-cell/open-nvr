# OpenNVR — yolov8-weights image

A ~20 MB Docker image that contains `yolov8n.onnx` pre-baked. Used by `docker-compose.yml`'s `yolov8-weights-init` container instead of the older runtime apt+pip+export path (which failed for operators behind ISP firewalls that filter `deb.debian.org` or `pypi.org`).

## You usually don't need to touch this

`docker compose -f docker-compose.yml up -d` Just Works:

- If `ghcr.io/open-nvr/yolov8-weights:v8.3.0` is published (the OpenNVR release path), Docker pulls it in ~5 seconds and the init container copies the ONNX into the volume.
- If the published image isn't reachable from your host (registry blocked, fresh repo before first release, etc.), Docker Compose's `build:` fallback kicks in and builds the image locally from the Dockerfile in this directory. First-time build takes ~10 min (dominated by the `ultralytics/ultralytics:8.3.40` base pull, ~3 GB). Subsequent `up -d` runs use the cached image instantly.

## When you might want to build it yourself

Three scenarios:

**1. Your network blocks ghcr.io specifically.** Rare but happens. Build on a machine with internet, transfer the image tarball:

```bash
cd examples/yolov8-weights
docker build -t yolov8-weights:v8.3.0 .

docker save yolov8-weights:v8.3.0 | gzip > /tmp/yolov8-weights.tar.gz
scp /tmp/yolov8-weights.tar.gz ubuntu@deploy-box:/tmp/

# On the deploy box:
docker load < /tmp/yolov8-weights.tar.gz
echo 'YOLOV8_WEIGHTS_IMAGE=yolov8-weights:v8.3.0' >> .env
docker compose -f docker-compose.yml up -d
```

**2. You have a fine-tuned YOLOv8 model you want to bake in.** Override the `YOLOV8_PT_URL` build arg to point at your `.pt`:

```bash
docker build \
    --build-arg YOLOV8_PT_URL=https://example.com/my-fine-tuned-yolov8n.pt \
    -t my-registry.local:5000/yolov8-weights:custom \
    .

docker push my-registry.local:5000/yolov8-weights:custom

# Then in your .env on the deploy box:
echo 'YOLOV8_WEIGHTS_IMAGE=my-registry.local:5000/yolov8-weights:custom' >> .env
```

Or copy your `.pt` directly into the build context:

```dockerfile
FROM ultralytics/ultralytics:8.3.40 AS exporter
WORKDIR /build
COPY my-fine-tuned-model.pt yolov8n.pt
RUN yolo export model=yolov8n.pt format=onnx opset=12 imgsz=640 simplify=False

FROM alpine:3.20
COPY --from=exporter /build/yolov8n.onnx /yolov8n.onnx
```

**3. You want to pin a specific Ultralytics release.** Change the base image tag in the Dockerfile:

```dockerfile
FROM ultralytics/ultralytics:8.4.0 AS exporter   # ← instead of 8.3.40
```

## Critical: keep these export flags unchanged

```
opset=12 imgsz=640 simplify=False
```

The OpenNVR YOLOv8 adapter is conformance-tested against this exact byte layout (see `docs/AI_ADAPTER_CONTRACT.md` §11.4). Changing any of them — especially `simplify=True` — folds constants, renames graph nodes, and shifts the model's sha256 fingerprint that lands in the audit chain. The adapter may also break or produce wrong outputs.

## CI publishes this automatically

`.github/workflows/build-yolov8-weights.yml` builds + pushes `ghcr.io/open-nvr/yolov8-weights:v<ultralytics-tag>` on every OpenNVR release tag. Multi-arch (amd64 + arm64). On main-branch pushes it publishes `:main` and `:sha-<short>` for traceability. The standard `docker compose -f docker-compose.yml up -d` picks up whatever's published on GHCR.
