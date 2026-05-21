# v0.1 fresh-clone smoke test

This is the manual smoke test for blocker B0.3 in `RELEASE_v0_1_PLAN.md`.
It must pass on a clean machine (no prior OpenNVR install, no cached
images) before we can tag v0.1.0.

The README in the rewrite will claim "`git clone` → `./start.sh` → open
the URL." This test is what makes that claim true.

---

## Test environments

Run the test in *each* of:

- [ ] **Ubuntu 22.04 LTS** — fresh VM (or a clean cloud instance). Linux
  uses `docker-compose.linux.yml` and host networking.
- [ ] **macOS** — fresh user account or VM. macOS uses
  `docker-compose.yml` and bridge networking.
- [ ] **Windows 11** — fresh user account. Windows uses `start.ps1` and
  `docker-compose.yml`.

If only one OS is available, run Ubuntu first — that's the largest
homelab segment.

---

## Prerequisites the user is allowed to install

Anything we list in the README's "Prerequisites" section is fair game.
Currently that's:

- Git
- Docker Desktop (macOS/Windows) or Docker Engine + Compose v2 (Linux)

If the test ever needs you to install something *not* in the README, the
README is wrong — capture the missing prereq and add it to the README
before tagging.

---

## The script

```bash
# 1. Clone the repo (no cd into a parent directory first — clone fresh)
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr

# 2. Run the smart launcher. First run = interactive installer.
./start.sh            # Linux / macOS
# or
.\start.ps1           # Windows

# 3. Watch the terminal output for the first-time setup banner. The
#    banner ends with a one-time setup token. Copy that token.

# 4. Open http://localhost:8000 in a browser. The first-time setup
#    page should appear. Paste the token, choose an admin password.

# 5. Confirm the dashboard loads.

# 6. Add one camera (test camera URL: rtsp://demo:demo@ipvmdemo.dyndns.org:5541/onvif-media/media.amp)
#    Confirm the live preview tile appears.

# 7. On the dashboard, find the "Enable AI Detection" banner.
#    Click it. Wait for ai-adapter image to pull (this can take a few
#    minutes the first time — that latency is fine, but the wait must
#    be visible in the UI).

# 8. Confirm an alert fires when a person walks into the camera frame.
#    Check the alert appears in /api/v1/alerts.

# 9. Confirm an example app can subscribe to NATS:
docker compose exec opennvr-core curl -sf nats://nats:4222/   # if exposed
#    or
docker compose run --rm alerts-subscriber python -m examples.alerts_subscriber --once

# 10. Cleanup
./start.sh down
```

---

## Pass criteria

Each step above must complete without manual intervention beyond what
the README documents. The acceptable behaviors:

1. `git clone` succeeds, no auth prompt for a public repo.
2. `./start.sh` runs the interactive installer without errors. Asks
   the documented questions (storage path, AI-adapter sibling check).
3. Secrets are generated automatically. `server/.env` is created with
   no placeholder values left behind.
4. Self-signed TLS certs are generated automatically for MediaMTX.
5. All containers come up healthy within 2 minutes of `up -d`.
6. The first-time-setup token banner appears in stdout. The banner
   includes an obvious "copy this verbatim" call-out.
7. The web UI is reachable at http://localhost:8000 without manual
   port-forwarding.
8. The setup form accepts the token + password. The admin account
   activates. Subsequent restarts skip the setup-token flow.
9. Camera add works. Live preview is visible within 30 seconds.
10. The "Enable AI Detection" button visibly pulls the ai-adapter
    image, starts the container, and provisions intrusion-detection
    against the camera. End state: "AI Detection: On" status banner.
11. A person walking into the frame fires an alert within 10 seconds.
12. The alert is visible at `/api/v1/alerts` and on the NATS subject
    `opennvr.alerts.kai-c.intrusion-detection.>`.
13. `./start.sh down` cleanly stops and removes all containers.

---

## Capture for the launch

While running the test, capture:

- [ ] Screen recording of steps 1–11 (used for the README demo GIF).
- [ ] Screenshots of: setup-token banner, first-time setup form,
  dashboard with one camera tile, the "Enable AI Detection" banner
  pre- and post-click, an alert in the UI, an alert payload in JSON.
- [ ] `start.sh` console output (used to verify wording).
- [ ] Approximate wall-clock time for each step (used to back the
  "60-second demo" claim in the README, or to soften it honestly if
  the real time is closer to 3 minutes).

---

## When a step fails

Each failure becomes its own ticket. The ticket title format:

```
v0.1 smoke-test fail: <step number> — <one-line description>
```

The ticket body must include:

- OS + Docker version (`docker --version`, `docker compose version`).
- The exact command that failed.
- Console output (full, not truncated).
- A repro: "starting from `git clone`, what is the shortest path to
  hit the same failure."

Don't apply a quick fix in-place during the smoke test — that
contaminates the test. File the ticket, restart from step 1 on a fresh
clone after the fix lands.

---

## Sign-off

When all three OS environments pass, sign off this doc with:

```
B0.3 sign-off: <YYYY-MM-DD>
- Ubuntu 22.04: pass / fail + ticket links
- macOS:        pass / fail + ticket links
- Windows 11:   pass / fail + ticket links
Demo GIF recorded: yes/no, path
Screenshots captured: yes/no, paths
```

Once signed off, v0.1 may proceed to the next phase (Examples).
