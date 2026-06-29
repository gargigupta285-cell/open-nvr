<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Camera-agent image requirements & publication status

What each profile needs on GHCR, so a fresh install actually starts. The
`camera-agent` image itself is **built from source** (`build:` in compose), not
pulled — only the adapters/runtimes below are pulled.

## Required images by profile

| Image | Profiles that need it | Source |
|-------|----------------------|--------|
| `ghcr.io/open-nvr/core` (+ mediamtx, nats, postgres, nginx, `yolov8-adapter`, `yolov8-weights`) | **all** (Tier 0) | published |
| `ghcr.io/open-nvr/whisper-adapter` | `camera-agent` (Sentinel voice) | published |
| `ghcr.io/open-nvr/piper-adapter` | `camera-agent` (Sentinel voice) | published |
| `ghcr.io/open-nvr/blip-adapter` | `camera-agent`, `camera-agent-standard` (default caption) | published |
| `ollama/ollama:0.21.2` | lite/standard/sentinel/demo | upstream |
| `ghcr.io/ggml-org/llama.cpp:server` | `camera-agent-llamacpp` | upstream |
| `ghcr.io/open-nvr/moondream-adapter` | `caption-moondream` (opt-in VQA) | **CI builds green; publishes on push/merge** |
| `ghcr.io/open-nvr/voice-adapter` | `camera-agent-combined` (opt-in) | **CI builds green; publishes on push/merge** |

The default paths (lite / standard / sentinel-modular / demo / llamacpp) need
only already-published or upstream images. The two new opt-in adapters are the
only additions.

## Publish nuance (PR build vs publish)
The `publish-images` workflow **builds** every adapter on a PR (smoke, `--load`,
no login) but only **publishes to GHCR** on the **push/merge** path (where it
logs in and `--push`). So a green PR run means *it builds* — it appears on GHCR
after the branch is pushed / the PR is merged.

## Verify on your machine
```bash
# exists on GHCR? (succeeds if published)
docker manifest inspect ghcr.io/open-nvr/moondream-adapter:latest
docker manifest inspect ghcr.io/open-nvr/voice-adapter:latest
# did the PUBLISH (push-event) job run?
gh run list -R open-nvr/ai-adapter --workflow publish-images.yml -L 5
# all org packages:  https://github.com/orgs/open-nvr/packages
```

## Moondream is "code-only" even once published
CI builds Moondream **without a model** (no build-arg). Provide the int8 `.mf.gz`
one of three ways (see `ai-adapter/adapters/moondream/README.md`):
1. bake at build (`--build-arg MOONDREAM_MODEL_URL=…`) — offline,
2. mount it into the `opennvr_moondream_models` volume, or
3. set `MOONDREAM_MODEL_URL` in `.env` → the adapter downloads it once on first
   start (pull-and-run; one-time fetch, not strict `local_only`).
