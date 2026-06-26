<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Promoting Moondream to the default caption adapter (retiring BLIP)

Moondream is *better and lighter* than BLIP for the camera-agent (visual Q&A +
captioning, ~<1.5 GB vs ~5.5 GB, no torch). The plan is to make it the **default**
and retire BLIP — but only **after it's verified on real hardware**. BLIP stays
the default until then so a fresh `Standard` install never breaks.

## Step 1 — Verify Moondream (do this first)

1. **Build** the adapter with a real model file (from the ai-adapter repo):
   ```bash
   docker build -f adapters/moondream/Dockerfile \
     --build-arg MOONDREAM_MODEL_URL=<url to moondream-0_5b-int8.mf.gz> \
     -t ghcr.io/open-nvr/moondream-adapter:local .
   ```
   Get the URL from https://moondream.ai/p/models (0.5B int8 is the edge default).
2. **Run + register** via the opt-in profile:
   ```bash
   CAPTION_ADAPTER=moondream docker compose -f docker-compose.tier0.yml \
     -f docker-compose.camera-agent.yml \
     --profile camera-agent-standard --profile caption-moondream up -d
   ```
3. **Smoke-test** at http://localhost:9100/demo — the answers that BLIP couldn't give:
   - "what is the person wearing?" → a real attribute answer (not a generic caption)
   - "what is he doing?" → an activity answer
   - "is the gate open?" → yes/no grounded in the frame
   - a plain "what do you see?" → a sensible caption (caption path still works)
4. **Check** latency is acceptable on CPU (a few seconds/turn) and KAI-C shows
   `moondream` registered (the `caption-adapter-register` log says "Registered").

Verified = the VQA answers are correct, captioning still works, and latency is OK.

## Step 2 — Promote to default (only after Step 1 passes)

Three small edits, gated on the image being **published to GHCR** (the CI matrix
builds `moondream-adapter` once the ai-adapter branch is merged):

1. **`.env.example`** — flip the default:
   ```
   CAPTION_ADAPTER=moondream
   ```
2. **`docker-compose.camera-agent.yml`** — run Moondream by default, BLIP opt-in:
   - `moondream-adapter` profiles: `[caption-moondream]` → `[camera-agent, camera-agent-standard]`
   - `blip-adapter` profiles: `[camera-agent, camera-agent-standard]` → `[caption-blip]`
3. Nothing else: `caption_adapter: ${CAPTION_ADAPTER}` and the
   `caption-adapter-register` init already follow the flag — they auto-register
   and use whatever `CAPTION_ADAPTER` says.

After that, `Standard`/`Sentinel` come up on Moondream by default; BLIP is still
available with `--profile caption-blip` + `CAPTION_ADAPTER=blip` for anyone who
wants the old captioner. Once you're confident, the `blip-adapter` service and
the ai-adapter `blip` adapter can be deleted entirely.

## Why gated, not flipped now
The Moondream image isn't published/verified yet. Defaulting to it before that
would make a fresh `Standard` install fail (missing image) — the opposite of the
"it just works" goal. This checklist makes the flip instant and safe once you
give the go-ahead.
