# Docker Quickstart

Get OpenNVR running in under five minutes using pre-built images from
GHCR. No source build, no toolchain, no manual model downloads.

If you only want to run the project (not modify it), this is the page you
want. Contributors should also read [CONTRIBUTING.md](CONTRIBUTING.md) and
[`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) for the bare-metal dev path.

## Prerequisites

- **Docker Engine 24+** with Compose v2 (Linux) or **Docker Desktop**
  (macOS / Windows).
- **8 GB RAM** recommended (4 GB will work, but YOLOv8 cold-start is
  tight).
- **20 GB free disk** — most of it is the AI adapter images and YOLO
  weights; the database and the app itself are small.
- A camera with **ONVIF or RTSP** support. Most modern IP cameras
  qualify. Phone webcams via apps like IP Webcam also work for testing.

## Tier 0 — NVR + YOLOv8

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
cp .env.example .env
./scripts/generate-secrets.sh --write          # Windows: .\scripts\generate-secrets.ps1 -Write
docker compose -f docker-compose.tier0.yml up -d
```

The generate-secrets script writes cryptographically random values into
`.env` for the four secrets the core validates at boot (`SECRET_KEY`,
`CREDENTIAL_ENCRYPTION_KEY`, `INTERNAL_API_KEY`, `MEDIAMTX_SECRET`) plus
the PostgreSQL password. **There are no shipped default credentials** —
the core refuses to boot if any of those four are placeholders or shorter
than the minimum length.

### First boot

On first start the core prints a **setup token** to its log. Grab it:

```bash
docker compose -f docker-compose.tier0.yml logs opennvr-core | grep -i 'setup token'
```

Open <http://localhost:8000>, paste the token on the setup screen, choose
an admin username and password, and you're in. The token is single-use —
subsequent restarts skip the setup flow because an admin already exists.

### What you should see

```bash
docker compose -f docker-compose.tier0.yml ps
```

```
NAME                                STATUS
opennvr_core                        Up (healthy)
opennvr_db                          Up (healthy)
opennvr_mediamtx                    Up (healthy)
opennvr_nats                        Up (healthy)
opennvr_yolov8_adapter              Up (healthy)
opennvr_yolov8_weights_init         Exited (0)         # one-shot, done
opennvr_mediamtx_certs_init         Exited (0)         # one-shot, done
```

The two `Exited (0)` rows are correct — they're init containers that
finish after their setup work is done.

### Endpoints

| Service | URL |
|---|---|
| Web UI | <http://localhost:8000> |
| API docs (OpenAPI / Swagger) | <http://localhost:8000/docs> |
| MediaMTX HLS playback | <http://localhost:8888> |
| MediaMTX WebRTC | <http://localhost:8889> |

The MediaMTX endpoints are gated by JWT — the frontend handles the
exchange transparently when you open a stream from the web UI.

## Adding the camera-agent voice overlay

Once Tier 0 is running, the camera-agent overlay layers Whisper STT,
Piper TTS, and an Ollama-hosted LLM on top so you can talk to your
cameras.

```bash
# Pull the LLM model the agent uses (~2 GB, one-time)
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent run --rm ollama-model-pull

# Bring up the overlay
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent up -d
```

Open <http://localhost:9100/demo>, click "Start", and ask
*"is there a person at the front door?"* — the agent fetches a live
frame, runs YOLOv8 + BLIP via tool calls, and speaks the answer back.

## Common operations

```bash
# Stop everything
docker compose -f docker-compose.tier0.yml down

# Tail logs (all services, or a specific one)
docker compose -f docker-compose.tier0.yml logs -f
docker compose -f docker-compose.tier0.yml logs -f opennvr-core

# Refresh to the latest published images
docker compose -f docker-compose.tier0.yml pull
docker compose -f docker-compose.tier0.yml up -d

# Restart a single service after editing .env
docker compose -f docker-compose.tier0.yml restart opennvr-core
```

## Customisation

### Change recording storage location

Recording volume is mapped via `RECORDINGS_PATH` in `.env`. Defaults to
`./recordings` (relative to the compose file). Set an absolute path for
production:

```bash
# Linux
RECORDINGS_PATH=/var/lib/opennvr/recordings

# macOS
RECORDINGS_PATH=/Users/Shared/opennvr-recordings

# Windows
RECORDINGS_PATH=D:/opennvr-recordings
```

Then `docker compose -f docker-compose.tier0.yml up -d` to remount.

### Use a different YOLOv8 model

The default is `yolov8n.onnx` (nano, ~6 MB) fetched from Hugging Face
on first boot. Override with `YOLOV8_WEIGHTS_URL` in `.env` to point at
a fine-tuned model or a private mirror:

```bash
YOLOV8_WEIGHTS_URL=https://example.com/internal/yolov8s-people.onnx
```

Wipe the cached weights volume to force a re-download after changing
the URL:

```bash
docker compose -f docker-compose.tier0.yml down
docker volume rm open-nvr_opennvr_yolov8_weights
docker compose -f docker-compose.tier0.yml up -d
```

### Change the admin password

Use the web UI: profile menu → change password. Don't try to do it via
`.env` — admin credentials live in the database, not in environment
variables.

### Increase log verbosity

```bash
LOG_LEVEL=DEBUG
```

in `.env`, then restart `opennvr-core`. Note this is verbose — only flip
it for troubleshooting.

## Troubleshooting

### Core refuses to start: "Refusing to boot on placeholder secret"

You skipped the `generate-secrets.sh` step or `.env` still has the
`dev_` defaults from `.env.example`. Re-run:

```bash
./scripts/generate-secrets.sh --write
docker compose -f docker-compose.tier0.yml up -d
```

### Port already in use

Another service is listening on 8000, 8888, 8889, or 5432. Find and
stop it, or override the host-side port in
`docker-compose.tier0.yml` (change `"127.0.0.1:8000:8000"` to
`"127.0.0.1:8080:8000"` for example).

### YOLOv8 adapter never goes healthy

Check the init container's logs:

```bash
docker compose -f docker-compose.tier0.yml logs yolov8-weights-init
```

A 503 from Hugging Face usually clears itself — the init container
retries five times with backoff. If it fails permanently, mirror
`yolov8n.onnx` to a URL you control and set `YOLOV8_WEIGHTS_URL`.

### Camera shows up but no detections

Check the inference event bus:

```bash
docker compose -f docker-compose.tier0.yml logs nats
docker compose -f docker-compose.tier0.yml logs opennvr-core | grep -i kai_c
```

KAI-C polls the YOLOv8 adapter on a per-camera schedule; if the schedule
is paused (e.g., camera is offline), no events fire. The web UI's
"Cameras" page shows the current schedule state.

### Disk filling up

```bash
docker system df              # see where the space went
docker system prune -a        # ⚠ removes ALL unused Docker data, not just OpenNVR
```

For OpenNVR specifically, recordings under `RECORDINGS_PATH` grow most
aggressively. Configure retention in the web UI's per-camera settings.

## Production deployment

Tier 0 is intended for the demo + homelab use case. For an internet-
facing deployment you'll want:

1. **Front the service with a real reverse proxy.** Caddy, Traefik, or
   nginx with a real TLS certificate. Don't expose 127.0.0.1:8000
   directly.
2. **Use host-mode networking on Linux.** ONVIF camera discovery uses
   UDP multicast, which doesn't traverse Docker's bridge driver. See
   [`docker-compose.linux.yml`](docker-compose.linux.yml).
3. **Restrict the MediaMTX listeners.** The Tier 0 compose binds them
   to 127.0.0.1; expose only via your reverse proxy with auth.
4. **Back up the database.** The `opennvr_db_data` volume holds your
   camera list, user accounts, and audit log.
5. **Configure retention.** Default retention is 7 days per camera —
   adjust per-camera in the web UI based on your disk budget.

Full production hardening checklist in
[`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md).

## Next steps

1. **Add a camera.** Web UI → Cameras → Add. ONVIF discovery is the
   easiest route; RTSP URL works if ONVIF isn't supported.
2. **Configure AI detection.** Web UI → AI Models. YOLOv8 is enabled by
   default in Tier 0; toggle per-camera as needed.
3. **Configure retention.** Web UI → Cameras → per-camera recording
   settings. Default is 7 days.
4. **Browse the API.** <http://localhost:8000/docs> — every endpoint is
   documented with example payloads.

## Support

- **User questions** → [GitHub Discussions](https://github.com/open-nvr/open-nvr/discussions)
- **Bug reports** → [GitHub Issues](https://github.com/open-nvr/open-nvr/issues)
- **Security** → [SECURITY.md](SECURITY.md)
- **Contributing** → [CONTRIBUTING.md](CONTRIBUTING.md)
