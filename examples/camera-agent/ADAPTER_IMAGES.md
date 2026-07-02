<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Camera-agent image requirements & publication status

What the camera-agent profiles need on GHCR, so a fresh install actually starts.
The `camera-agent` image itself is **built from source** (`build:` in compose),
not pulled — only the adapters/runtimes below are pulled.

## Required images

| Image | Profile(s) | Source |
|-------|-----------|--------|
| `ghcr.io/open-nvr/core` (+ mediamtx, nats, postgres, nginx, `yolov8-adapter`, `yolov8-weights`) | both (standard stack) | published |
| `ghcr.io/open-nvr/blip-adapter` (default caption) | `camera-agent`, `camera-agent-chat` | published |
| `ollama/ollama:0.21.2` | `camera-agent`, `camera-agent-chat` | upstream |
| `ghcr.io/open-nvr/whisper-adapter` | `camera-agent` (voice only) | published |
| `ghcr.io/open-nvr/piper-adapter` | `camera-agent` (voice only) | published |

Both the **voice** (`camera-agent`) and **chat** (`camera-agent-chat`) profiles
need only already-published or upstream images, so a fresh clone comes up with
nothing to build but the agent itself. Chat skips Whisper/Piper.

## Publish nuance (PR build vs publish)
The `publish-images` workflow **builds** every adapter on a PR (smoke, `--load`,
no login) but only **publishes to GHCR** on the **push/merge** path (where it
logs in and `--push`). So a green PR run means *it builds* — it appears on GHCR
after the branch is pushed / the PR is merged.

## Verify on your machine
```bash
# exists on GHCR? (succeeds if published)
docker manifest inspect ghcr.io/open-nvr/whisper-adapter:latest
docker manifest inspect ghcr.io/open-nvr/blip-adapter:latest
# all org packages:  https://github.com/orgs/open-nvr/packages
```
