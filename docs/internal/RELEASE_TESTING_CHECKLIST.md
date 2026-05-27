# Release Testing Checklist — v0.1.0

Internal document. The team runs this against the v0.1.0 release
candidate before tagging public release. Treat this as the contract
between "we shipped" and "the public can actually use this."

Time budget: a thorough first pass takes ~3 hours on a single host.
Skip nothing on the BLOCKERS list — the cost of catching a regression
post-launch is much higher than the cost of running this through twice.

## Pre-test environment

Each tester runs against:

1. **A clean host** — fresh Ubuntu 22.04 / 24.04 LTS or macOS 14+, no
   prior OpenNVR state. Don't test on the dev machine where you wrote
   the code; muscle memory hides regressions.
2. **A real IP camera** — ONVIF discoverable, RTSP / RTSPS reachable.
   Cheap consumer cameras are more representative of the user
   environment than enterprise hardware; include at least one of each
   if you can.
3. **A second camera** for the multi-camera scenarios. Same posture
   as the first.
4. **A separate machine to test browser playback from** — the host the
   OpenNVR install runs on shouldn't be the same machine that's
   watching the WebRTC stream. That catches the "works on
   localhost, breaks on LAN" class of bugs.

---

## BLOCKERS — must pass before tagging

If any of these fail, fix them and re-run the full checklist. Don't
ship around a BLOCKER.

### Tier 0 install

- [ ] `git clone https://github.com/open-nvr/open-nvr.git` succeeds.
- [ ] `cp .env.example .env` produces a working starting `.env`.
- [ ] `./scripts/generate-secrets.sh --write` produces strong values
      for `SECRET_KEY`, `INTERNAL_API_KEY`, `CREDENTIAL_ENCRYPTION_KEY`,
      `MEDIAMTX_SECRET`, `POSTGRES_PASSWORD`.
- [ ] `./scripts/generate-secrets.ps1 -Write` produces the same on
      Windows (use a Windows VM if needed).
- [ ] `docker compose -f docker-compose.tier0.yml up -d` pulls
      pre-built images from GHCR without building anything locally
      (verify by checking `docker images` — no `<none>` tags from
      local build).
- [ ] All services reach `healthy` or `Up` state within 5 minutes
      total wall-clock time on a typical home broadband link.
- [ ] `http://localhost:8000` loads the OpenNVR web UI.
- [ ] First-boot setup-token flow works — token printed to
      `opennvr-core` container logs, paste into UI, admin user
      created.
- [ ] Adding an ONVIF camera through the web UI succeeds.
- [ ] Adding a camera by manual RTSP URL succeeds.
- [ ] Live view shows the camera feed (WebRTC primary, HLS fallback).
- [ ] YOLOv8 detection overlays appear on the live view within
      ~30 seconds of camera connection.

### Camera-agent overlay

- [ ] `docker compose -f docker-compose.tier0.yml -f docker-compose.camera-agent.yml --profile camera-agent run --rm ollama-model-pull` succeeds and pulls the LLM model.
- [ ] `docker compose -f docker-compose.tier0.yml -f docker-compose.camera-agent.yml --profile camera-agent up -d` brings up the additional services within 5 minutes.
- [ ] `http://localhost:9100/demo` loads. Click "Start" — browser asks
      for microphone permission.
- [ ] Speak: "Is there a person at the front door?" — agent responds
      with a Piper TTS reply within 15 seconds (latency budget
      tolerant of cold-start).
- [ ] The reply is grounded in a real frame — kill the camera, ask
      again, verify the agent says something equivalent to "I can't
      see anything" rather than confidently hallucinating.
- [ ] Reconnect the camera, ask the question again, verify the agent
      now answers correctly.

### Security defaults

- [ ] Edit `.env` to put `SECRET_KEY=placeholder`. Restart the core
      container. Verify it refuses to start with a clear error message.
- [ ] Same for `INTERNAL_API_KEY`, `CREDENTIAL_ENCRYPTION_KEY`,
      `MEDIAMTX_SECRET`. All four secrets must independently refuse
      to boot on placeholder.
- [ ] `curl http://localhost:8000/api/v1/cloud/...` (any cloud route)
      returns HTTP 403 with the default `DEPLOYMENT_MODE=offline`.
- [ ] Set `DEPLOYMENT_MODE=hybrid` (or `cloud`). Restart. Verify the
      cloud route now succeeds and the boot log records the
      deviation audit event.
- [ ] Reset `DEPLOYMENT_MODE=offline`. Verify the route returns 403
      again on the next request.
- [ ] Separately, verify `AI_SOVEREIGNTY=local_only` (default) causes
      KAI-C to refuse to register an adapter declaring
      `network_egress` permissions (manually craft a test adapter that
      advertises this in `/capabilities` and confirm the refusal lands
      in the audit log).
- [ ] No default admin password in the running container — try
      `admin / admin`, `admin / admin123`, `admin / SecurePass123!`.
      All three must fail.

### Audit chain

- [ ] Trigger an inference (e.g. by enabling AI detection on a camera).
- [ ] Confirm an `X-Correlation-Id` is present in the alert that
      flows through to NATS (`opennvr.alerts.*`).
- [ ] Confirm the same correlation_id appears in the audit log
      (`$KAI_C_AUDIT_LOG`, default `/var/log/opennvr/kai-c-audit.jsonl`).
- [ ] Confirm the inference event records `model.fingerprint` (sha256).
- [ ] Manually mutate the model weights file on disk (touch the bytes
      via `dd`). Wait 60s. Verify an `adapter.fingerprint_mismatch`
      event appears in the audit log.
- [ ] Replace a model weights file with one with completely different
      content. Wait 60s. Verify drift event fires.

### RTSP fast-path

- [ ] With Tier 0 running, `tcpdump -i any port 8554` on the
      MediaMTX host shows internal traffic to / from the YOLOv8
      adapter container during inference.
- [ ] Confirm the inference URL KAI-C uses includes `?jwt=` query
      string (check `opennvr-core` logs at DEBUG level).
- [ ] Set `INFERENCE_USE_MEDIAMTX_TAP=false` in `.env`, restart.
      Confirm inference still works using direct camera RTSP.
- [ ] Restore default (`true`). Confirm inference resumes via the
      MediaMTX tap.

### Adapter contract / SDK

- [ ] Pull `ghcr.io/open-nvr/yolov8-adapter:latest` standalone, run
      it, hit `/health`, `/capabilities`, `/infer` with a test image.
      All four endpoints respond correctly.
- [ ] Repeat for `piper-adapter`, `whisper-adapter`,
      `fast-plate-ocr-adapter`, `insightface-adapter`, `blip-adapter`,
      `bytetrack-adapter`. Seven adapters, seven manual smoke checks.
- [ ] Run the conformance suite against one of them:
      `python -m conformance http://localhost:9002 --token <token>`.
      Pass.
- [ ] In the ai-adapter repo, run
      `./templates/adapter-template/scaffold.sh test-scaffold 9099 TEXT`.
      Confirm the generated adapter compiles, tests pass, and
      `uvicorn adapters.test_scaffold.main:app` boots with healthy
      `/health`.

---

## IMPORTANTS — should pass; if they fail, decide before tagging

### Multi-camera scenarios

- [ ] Add three cameras through the web UI. Verify each shows
      bounding boxes from its own YOLOv8 inference.
- [ ] Trigger an alert from one camera. Verify it's correlated to
      that camera's path in the audit log, not bleeding to other
      cameras' track IDs.
- [ ] Disable AI on one camera mid-stream. Verify it's still
      recording but no longer producing inference events.

### Recording + playback

- [ ] Verify camera recordings appear in `RECORDINGS_PATH` (default
      `./recordings`) as fmp4 segments.
- [ ] Browse to the playback view in the web UI, select a recording
      from the last hour, scrub the timeline, confirm playback works.
- [ ] Export an MP4 via the per-segment menu. Verify the downloaded
      file plays in VLC.

### Examples gallery

For each shipped example app (`intrusion-detection`,
`loitering-detection`, `inference-listener`, `alerts-subscriber`,
`license-plate-recognition`, `smart-doorbell`, `package-delivery`,
`camera-agent`, `home-assistant-relay`):

- [ ] `cd examples/<example> && uv sync --extra dev` succeeds.
- [ ] `pytest` passes the test suite for that example.
- [ ] `cp config.example.yml config.yml`, edit minimally, run the
      main entry-point. Service boots.
- [ ] Trigger the relevant event (zone violation, dwell, NATS
      event, etc.) and verify the example reacts as documented.

### Documentation

- [ ] Read README.md top to bottom as a first-time user. Note any
      friction.
- [ ] Read DOCKER_QUICKSTART.md top to bottom. Run the commands
      verbatim. Should work.
- [ ] Open `docs/COMPLIANCE.md` and verify the linked code paths
      (`kai_c_service.py:_resolve_inference_rtsp_url`, etc.) actually
      exist in the repo.
- [ ] Open `docs/GOVERNMENT_DEPLOYMENT.md` and verify the printable
      one-pager actually fits a printout (no awkward page breaks
      mid-table).
- [ ] Open `docs/COMPARISONS.md` and verify the Frigate / ZoneMinder /
      Shinobi claims still match upstream reality at the time of
      tagging.
- [ ] Open `docs/USE_CASES.md` and confirm "shipped seven" matches
      the adapter count.
- [ ] Walk through `docs/ROADMAP.md` — verify shipped items match
      the actual release.

### Build-from-source path (for contributors)

- [ ] `./start.sh build` (Linux/macOS) — clean install on a host
      without pre-built images works end to end.
- [ ] `.\start.ps1 build` (Windows) — same.
- [ ] Bare-metal dev shell from `docs/LOCAL_SETUP.md` — all five
      terminals start, full stack reachable.

### Adapter publish workflow (CI surface, not local)

- [ ] Push a `v0.1.0-rc.1` test tag to a fork or feature branch.
      Verify `.github/workflows/publish-images.yml` builds all seven
      adapter images.
- [ ] Verify the multi-tag scheme (`:0.1.0-rc.1`, `:0.1.0-rc`, etc.)
      lands correctly on GHCR.
- [ ] Verify `ghcr.io/open-nvr/core` builds and pushes the core image.
- [ ] After RC validation: delete the RC tag and push the real
      `v0.1.0` tag.

---

## NICE-TO-HAVE — log it for v0.1.1 if these fail

### Performance baseline

These aren't blockers but capturing the numbers helps the launch
narrative. Record:

- [ ] CPU usage on a Raspberry Pi 5 / Intel NUC / similar with one
      1080p camera + YOLOv8 detection enabled. Steady state.
- [ ] CPU usage with the camera-agent overlay running on top.
- [ ] Inference latency (model load to first detection ready) for
      each of the seven adapters.
- [ ] Disk usage growth rate per camera at 1080p / 24×7 recording
      (extrapolate to retention budget).
- [ ] Memory usage of the per-camera ByteTrack tracker after one
      hour of activity.

These numbers belong in the DOCKER_QUICKSTART.md "Production
deployment" section in a future patch — for v0.1.0 just record them.

### Hardware coverage

- [ ] At least one Raspberry Pi 5 install runs end-to-end (homelab
      audience reference).
- [ ] At least one mini-PC (Intel N100 / similar) install works
      (SMB / municipal reference).
- [ ] At least one x86 server with NVIDIA GPU works for the GPU
      paths (defence / critical-infra reference). Note any
      adapter-specific GPU detection that doesn't fire.

### Browser compatibility

- [ ] Firefox latest — full WebRTC + camera-agent voice path.
- [ ] Chrome / Edge latest — same.
- [ ] Safari latest — webrtc may be more limited; document any
      differences in DOCKER_QUICKSTART or USER_MANUAL.
- [ ] Mobile Chrome / Safari — at minimum the dashboard renders
      and live view plays. Voice-agent on mobile is optional for
      v0.1.0.

---

## Failure-mode rehearsals

These are the launch-credibility test: what happens when things go
wrong.

- [ ] Pull the network cable mid-inference. Camera-agent should
      keep working (local AI). External alerts should queue.
- [ ] Stop the MediaMTX container. KAI-C should mark adapter
      `unavailable` after ~3 health probes. Restart MediaMTX —
      adapter should recover within 60s.
- [ ] Kill the postgres container. Core should report DB
      connection errors clearly. Restart postgres — service should
      reconnect without manual intervention.
- [ ] Fill the recordings disk to 95% capacity. Verify the
      retention policy actually evicts old recordings as documented.
- [ ] Crash the YOLOv8 adapter (`docker kill opennvr_yolov8_adapter`).
      Verify the audit log shows the unavailability event, alerts
      stop firing for that adapter, and the rest of the stack
      keeps running.

---

## Sign-off

Each tester records:

```
Tester: ________
Hardware: ________
Date: ________
Tier 0 install pass: y/n + notes
Camera-agent pass: y/n + notes
Security defaults pass: y/n + notes
Audit chain pass: y/n + notes
RTSP fast-path pass: y/n + notes
Adapter contract pass: y/n + notes
Multi-camera pass: y/n + notes
Recording/playback pass: y/n + notes
Examples gallery pass: y/n + notes
Docs walkthrough pass: y/n + notes
Build-from-source pass: y/n + notes
Failure-mode rehearsals pass: y/n + notes
Overall verdict: ship / fix-and-retest / block
```

Two independent testers must sign off before the v0.1.0 tag is
pushed. Don't accept "I ran half of it on my machine" as
sign-off — the BLOCKERS section is non-negotiable end-to-end.

## After sign-off

Steps to publish:

1. Clean up throwaway scaffold artefacts in the ai-adapter repo:
   ```bash
   rm -rf adapters/demo_stub adapters/review_stub \
          tests/test_demo_stub_service.py tests/test_review_stub_service.py
   ```
2. Final review of `docs/internal/RELEASE_NOTES_v0.1.0.md` — paste
   into a fresh GitHub release draft for both repos.
3. Tag the release: `git tag -s v0.1.0 -m "OpenNVR v0.1.0"` then
   `git push origin v0.1.0`.
4. Watch the `publish-images.yml` workflow run; verify all images land
   on GHCR with the right tags.
5. Publish the GitHub release once images are confirmed.
6. Execute the launch sequence per [`GTM_PLAN.md`](GTM_PLAN.md):
   - Week 1: selfh.st short blurb submitted, demo video editing in progress.
   - Week 2: demo video shipped, r/selfhosted + r/homelab posts.
   - Week 4–5: HN Show HN post with embedded demo video.
