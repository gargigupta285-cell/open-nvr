# OpenNVR v0.1 — Initial Release Plan

> **Status:** draft v2. This document is internal — it is the working plan
> the team uses to drive toward the v0.1.0 release. Nothing in here ships in
> public-facing docs. Internal milestone codes (M0, A2.x, B1, C1, etc.) live
> here and in git history only — they get scrubbed out of every README,
> CONTRIBUTING, and user-facing doc before tagging.

---

## 1. Guiding principles (decided)

1. **Blockers first.** No release work happens until every blocker in §3 is
   green on a clean machine. A stranger running `git clone → ./start.sh`
   gets a working stack or we have failed.
2. **Sharp, professional public copy.** READMEs read like a product, not a
   dev journal. No internal slice IDs leak into public docs.
3. **Secure by default, exciting by default.** All security hardening is on
   out of the box. AI detection is off by default but a single click away
   from the UI. The README leads with this story.
4. **Differentiation is the headline.** We don't compete with Frigate on
   "best object-detection NVR." We lead on sovereignty, audit trail,
   pluggable adapter contract, offline-by-default, and a real event bus —
   things Frigate / Shinobi / Viseron don't claim.
5. **Examples are the demo.** The 4 example apps are the screenshot grid on
   the README, and the reason a homelab developer clicks Star.

---

## 2. Inventory — current state

### 2.1 Repos & branches

| Repo | Branch | Versioned as | Tags | CI |
|------|--------|--------------|------|----|
| `open-nvr` | `arch-rev` (+16 ahead of main) | server 1.0.0, kai-c 1.0.0 | none | none |
| `ai-adapter` | `arch-rev` (+1 ahead of main) | app 2.0.0, SDK 1.0.0 | none | publish-sdk only |

We reset every `pyproject.toml` version to **`0.1.0`** at release time so
semver promises start honestly.

### 2.2 Components — release-readiness

| Component | Status | Gaps for v0.1 |
|-----------|--------|---------------|
| open-nvr core server | Stable. Docker + bare-metal install paths work. Security hardening landed. | No CI. No fresh-clone smoke test. |
| KAI-C middleware | Stable. HTTP + WS proxy + NATS publisher landed. | **9 pytest errors in test_registry.py.** FastAPI `on_event` deprecation noise. |
| React frontend | Functional. First-time-setup UI landed. | Needs an "Enable AI Detection" button on the dashboard (decision §1.3). |
| ai-adapter app | Stable. 3 reference adapters on the SDK. | No CI for the app itself. |
| opennvr-adapter-sdk | Publishable. PyPI workflow committed. | Never actually published. First tag = first publish. |
| AI Adapter Contract | Frozen-ish. | Decide which sections we promise wire-compat on (§4 below). |
| Existing 4 examples | Stable. 157 tests passing. | None — release-ready. |
| New examples (LPR, doorbell, package, HA-relay) | Not built. | All four ship in v0.1 — see §5. |
| Root README (both repos) | Install-heavy, leaks internal slice IDs. | Full rewrite — see §6. |
| CHANGELOG.md | Missing on both | Required for v0.1.0. |

---

## 3. Release blockers (must be green before R1 starts)

These are the items where a stranger on a fresh clone hits something
visibly broken. They block tagging.

### B0.1 — kai-c test_registry.py pytest errors

9 tests in `kai-c/test/test_registry.py` are sync functions requesting the
async `registry` fixture. `pytest -q` ends with **9 ERRORs** today.
Already emitting `PytestRemovedIn9Warning`.

**Fix:** mark the affected tests `@pytest.mark.asyncio` and make the
fixture async-only, or convert the fixture to sync and the affected
methods to non-async. (Choose at implementation time.)

### B0.2 — FastAPI `on_event` deprecation

`@app.on_event("startup")` / `("shutdown")` in `kai-c/main.py` (and
likely ai-adapter root). Floods every test run with `DeprecationWarning`.

**Fix:** migrate to FastAPI `lifespan` async context manager.

### B0.3 — Fresh-clone smoke test

Nobody has actually run `git clone → ./start.sh` on a clean VM and watched
it succeed end-to-end. Until we do, the README's claims are not testable.

**Fix:** Spin up an Ubuntu 22.04 + macOS VM. Run start.sh. Every friction
point becomes its own ticket. Bonus: this gives us the 30-second demo GIF
we need for the README and the launch posts.

### B0.4 — Confirm B1-alerts on `main`

The B1-alerts commit (`ec4d7f8` on `arch-rev`) is committed but the PR
from `arch-rev → main` hasn't been opened in GitHub UI yet. Not a blocker
for further work on `arch-rev`, but blocks tagging from `main`.

---

## 4. v0.1 cut-line — what we promise

**Stable in v0.1.0** (wire-compat / behavior guaranteed until v0.2):

- AI Adapter Contract v1: transport, health/capabilities, all five result
  schemas (DetectionResult / RecognitionResult / CaptionResult /
  TranscriptionResult / SynthesizedAudioResult), WebSocket streaming,
  error envelope, alert wire shape, NATS subject scheme.
- `open-nvr/server` REST API at `/api/v1/*` and OpenAPI at `/docs`.
- KAI-C proxy paths `POST /api/v1/infer/{adapter}` and `WS /api/v1/infer/{adapter}/stream`.
- NATS subject scheme `opennvr.inference.*` and `opennvr.alerts.*`.
- `opennvr-adapter-sdk` public API: `AdapterService`, `AdapterApp`,
  `ServiceError`, `BODY_BYTES_KEY`, result models.

**Experimental in v0.1.0** (may break in v0.2 — labeled in docs):

- Sovereignty modes other than `local_only`.
- The CLI surface in `ai-adapter/cli.py`.
- Frontend component layout.
- Anything under `examples/` is reference, not a stability promise.

**Punted to v0.2 or later:**

- Helm charts / k8s manifests.
- Cloud-mode polish (`DEPLOYMENT_MODE=hybrid` / `cloud`).
- Multi-node clustering.
- Mobile push apps.

---

## 5. New examples for v0.1 (decided)

All four ship in v0.1, each as its own slice with the same quality bar as
intrusion-detection (copy-as-template, config.example.yml, tests, README).

### 5.1 License Plate Recognition (`examples/license-plate-recognition/`)

- **Hook:** "Detect every car on your driveway, log every plate."
- **Stack:** YOLOv8 vehicle detection → crop → OCR adapter (new
  `opennvr-adapter-paddleocr` or similar) → NATS alert with `plate_text`,
  `vehicle_color`, `confidence`.
- **Differentiator vs Frigate LPR:** ours is pluggable — swap the OCR
  adapter for cloud Vision API in `hybrid` mode without rewriting code.
- **Demo material:** screenshot of a driveway feed with a detection
  overlay + a sample alert payload in the README.

### 5.2 Smart Doorbell (`examples/smart-doorbell/`)

- **Hook:** "Know who's at the door — family, friend, or stranger."
- **Stack:** InsightFace recognition adapter (already in ai-adapter) →
  classify as `known` / `unknown` → push to Telegram / ntfy / webhook
  with a snapshot.
- **Differentiator:** the enrollment flow is a single REST call, no UI
  required. Show that in the README — most competitors require a desktop
  app.
- **Demo material:** known-person snapshot vs unknown-person snapshot,
  Telegram message screenshot.

### 5.3 Package Delivery Detection (`examples/package-delivery/`)

- **Hook:** "Alert me when a package arrives — and when it leaves."
- **Stack:** YOLOv8 `box`/`package` class (or fine-tuned) on a porch
  ROI → state machine for arrive / linger / disappear → alert with
  duration.
- **Differentiator:** state machine is in user code (copy-as-template),
  not buried in a config UI. Devs can fork and tune.
- **Demo material:** time-series chart of porch occupancy, an alert
  fired when the package disappears.

### 5.4 Home Assistant Relay (`examples/home-assistant-relay/`)

- **Hook:** "Every OpenNVR alert in your Home Assistant dashboard."
- **Stack:** NATS alert subscriber → MQTT publisher to HA broker, or
  direct webhook to HA REST API → device_class + entity_id mapping.
- **Differentiator:** every HA user can drop OpenNVR into their existing
  dashboards in 5 minutes. Massive distribution multiplier.
- **Demo material:** screenshot of the HA dashboard with OpenNVR cameras
  + alert sensors, blueprint for an HA automation.

### 5.5 The existing 4 stay

Existing examples (intrusion-detection, loitering-detection,
inference-listener, alerts-subscriber) keep shipping. They serve as the
"building blocks" tier — minimal, readable, copy-as-template starting
points. The 4 new examples are the "complete app" tier.

---

## 6. README rewrite — `open-nvr/README.md`

This is the single highest-leverage file in the release. It is the front
door. The current README is 284 lines, install-heavy, and references
internal slice work. It gets rewritten end-to-end.

### 6.1 Public docs scrub rule

Before tagging, every public-facing markdown file is searched for these
internal tokens and either replaced or moved to `docs/internal/`:

- `M0`, `M1a`, `M1b`, `M1c` and their `-fixup`/`-selfrev` variants
- `A2.1` through `A2.5b` and `A2.4d`, `A2.5b-fix`, etc.
- `B1`, `B1-alerts`, `C1`
- "V-001" through "V-022" vulnerability IDs (kept inside
  `docs/SECURITY_ARCHITECTURE.md` because that doc is a paper-to-code
  map — but referenced from the README as "see security architecture",
  not by V-number)
- "Zenodo DOI" only inside the security doc, not the front-door README
- "arch-rev" — never in user-facing docs

### 6.2 Proposed structure (top to bottom)

```
1. Hero (3 lines + badges + demo GIF)
2. The 60-second demo (single fenced code block)
3. Why OpenNVR — what makes us different (4-5 bullets)
4. Feature grid (icons + 1 line each)
5. Comparison table (Frigate / Shinobi / Viseron / Agent DVR / iSpy)
6. Architecture diagram (link to /docs)
7. Quick install (Docker)                  ← USERS
8. Build & run from source                 ← DEVELOPERS
9. Add a new AI adapter (3 steps)          ← DEVELOPERS
10. Examples gallery (the new 4 + existing 4)
11. Contributing in 5 steps                ← CONTRIBUTORS
12. Community (Discord, awesome-selfhosted badge, GitHub Discussions)
13. Security & posture (link only)
14. License & contact
```

### 6.3 Hero text (draft)

```
# OpenNVR

**Self-hosted, AI-powered video surveillance — designed for sovereignty,
built for developers.**

OpenNVR is an open-source NVR with a pluggable AI adapter ecosystem,
default-deny network posture, and an end-to-end audit trail from camera
to alert. Bring your own model. Own your footage. Deploy anywhere.

[ badges: stars · docker pulls · license · CI · discord · awesome ]
[ 30-second demo GIF ]
```

### 6.4 Comparison table (proposed rows)

| Concern | Frigate | Shinobi | Viseron | OpenNVR |
|---------|---------|---------|---------|---------|
| Pluggable AI models | Coral / OpenVINO compiled-in | Plugin system | YOLO + face | **Open contract — any model behind REST/WS** |
| Audit chain | Event DB | Logs | Logs | **Per-request `X-Correlation-Id` across the stack** |
| Sovereignty enforcement | None | None | None | **Offline-first; cloud routes 403 by default** |
| Model fingerprint drift detection | No | No | No | **sha256 polled every 60s; emits audit events** |
| Event bus | Internal MQTT | none | webhook | **NATS, public subject scheme, copy-as-template subscribers** |
| Multi-tenant fairness | Single-process | Multi-monitor | Single-process | **Per-camera fair queuing declared in `/capabilities`** |
| TLS defaults | User-managed | User-managed | User-managed | **RTSPS / HLS-TLS / WebRTC-TLS on by default** |
| First boot security | Open | Open | Open | **One-time admin setup token, no default password** |
| Frontend | Web UI | Web UI | Web UI | **Web UI + JSON API + reusable React shell** |
| License | MIT | GPLv3 | MIT | **AGPLv3 (required for the sovereignty story)** |

(Wording will be honest — Frigate is faster on Coral, Viseron has nicer
zone UX. We credit them where they're better.)

### 6.5 60-second demo (target text)

```bash
git clone https://github.com/open-nvr/open-nvr.git
cd open-nvr
./start.sh                                  # Linux / macOS
# Open http://localhost:8000 and paste the setup token from the terminal.
# Click "Enable AI Detection" on the dashboard to turn on intrusion alerts.
```

Three lines + one click. If this doesn't work end-to-end on a clean VM
on the first try, B0.3 says we don't tag.

### 6.6 ai-adapter README

Smaller treatment, same principles. Removes the "Why use ai-adapter"
section's internal jargon, keeps the value table but rewrites to read
like docs for a product. Cross-links to open-nvr README.

---

## 7. Default-boot experience (decided)

### 7.1 Security posture — fully on by default

No change from current state. Every hardening that landed in M0–M1c
stays on:

- Strong-secret validators (refuses placeholder values).
- Loopback-only MediaMTX (unless `ALLOW_REMOTE_MEDIAMTX=true`).
- RTSPS / HLS-TLS / WebRTC-TLS by default; plaintext requires audit-logged opt-in.
- Offline mode by default (cloud routes 403 unless `DEPLOYMENT_MODE` flipped).
- Sovereignty `local_only` by default.
- One-time setup token; no default admin password.
- Per-camera transport_security policy.

The README lands hard on this: "Every security feature is on by default.
You don't configure security — you configure exceptions."

### 7.2 AI detection — off by default, one click on

- `.env.example` ships with `AI_ENABLED=false`.
- Dashboard surfaces a prominent "**Enable AI Detection**" button on
  first login (after first-time-setup).
- Clicking flips `AI_ENABLED=true`, starts ai-adapter container, pulls
  model weights, restarts kai-c, registers intrusion-detection example
  against camera 1.
- README explains the one-click flow with a screenshot.
- This respects users on low-end hardware (Pi 4, NAS) — they get a fast
  boot, no surprise GB-scale downloads — while keeping the wow factor
  one click away.

### 7.3 Implementation surface

- Backend: new endpoint `POST /api/v1/system/ai/enable` that flips the
  env var, kicks the ai-adapter profile in compose, and provisions the
  default example. Audit-logged.
- Frontend: button + status banner on dashboard.
- Docs: README screenshot of the button + one-paragraph explanation.

---

## 8. Distribution & launch (decided)

All four channels in play.

### 8.1 GitHub Release + README (R10a)

- Tag v0.1.0 on both repos.
- GitHub Releases with auto-generated notes + curated summary.
- Pin the release on the repo Home tab.
- Set up GitHub Discussions with seeded "Welcome / FAQ / Show & Tell" threads.

### 8.2 Show HN + /r/homelab + /r/selfhosted (R10b)

- 30-second demo GIF ready (recorded during B0.3 smoke test).
- "Show HN: OpenNVR — self-hosted AI surveillance with a pluggable
  adapter contract" — 2-paragraph framing.
- Coordinated post within 2 hours across HN + reddits.
- Author monitors comments for the first 24h.

### 8.3 YouTube / blog outreach (R10c)

- Pitch packet: 1-page brief, install script, sample footage, demo
  config, contact email.
- Targets: TechnoTim, Christian Lempa, NetworkChuck, Awesome Open
  Source, Self-Hosted podcast (Jupiter Broadcasting), Lawrence Systems.
- Offer: free preview before launch, exclusivity on the launch-week video.

### 8.4 Discord + Awesome lists (R10d)

- Discord server: #welcome, #install-help, #adapter-development,
  #show-and-tell, #announcements. Moderation rules from day one.
- PRs to `awesome-selfhosted`, `awesome-opensource`, `awesome-nvr` (if
  exists, otherwise create).
- LinuxServer.io community Docker image proposal — they reach a huge
  homelab audience.

---

## 9. Punch-list — sequenced slices

Slices in order. Each is its own peer-reviewed PR unless flagged manual.

### Phase 0 — Blockers (must be done before R1 starts)

| # | Slice | Repo | Output |
|---|-------|------|--------|
| B0.1 | Fix 9 pytest_asyncio errors in `kai-c/test/test_registry.py` | open-nvr | `pytest` is green on fresh clone |
| B0.2 | Migrate FastAPI on_event → lifespan handlers | both | No DeprecationWarning in test output |
| B0.3 | Fresh-clone smoke test on Ubuntu + macOS VM; document every friction point as its own ticket | open-nvr | Demo GIF + working install confirmed |
| B0.4 | Open the `arch-rev → main` PR for the B1-alerts commit (user action in GitHub UI) | open-nvr | Main has all the work |

### Phase 1 — Examples (the screenshot grid)

| # | Slice | Repo | Output |
|---|-------|------|--------|
| E1 | License Plate Recognition example | open-nvr + ai-adapter (new OCR adapter) | New example + new adapter |
| E2 | Smart Doorbell example | open-nvr | New example using existing face adapter |
| E3 | Package Delivery Detection example | open-nvr | New example + porch ROI state machine |
| E4 | Home Assistant Relay example | open-nvr | New example, MQTT + webhook paths |

### Phase 2 — Default boot

| # | Slice | Repo | Output |
|---|-------|------|--------|
| D1 | "Enable AI Detection" backend endpoint + audit | open-nvr | `POST /api/v1/system/ai/enable` |
| D2 | "Enable AI Detection" UI button + status banner | open-nvr (app/) | One-click AI on |
| D3 | Default `.env.example` flips AI off, security defaults documented | open-nvr | Lean default boot |

### Phase 3 — Docs + scrub

| # | Slice | Repo | Output |
|---|-------|------|--------|
| R1 | Rewrite root README (open-nvr) + COMPARISON.md + QUICKSTART.md | open-nvr | The front door |
| R2 | Rewrite root README (ai-adapter) + cross-link | ai-adapter | Front door part 2 |
| R3 | `examples/README.md` catalogue + per-example "what you'll build" headers | open-nvr | One landing page for the 8 examples |
| R4 | Scrub internal slice IDs from all public docs; move history to `docs/internal/HISTORY.md` | both | Public docs read like a product |
| R5 | CHANGELOG.md seeded with the journey (no slice IDs in public copy) | both | Required for v0.1.0 |

### Phase 4 — CI

| # | Slice | Repo | Output |
|---|-------|------|--------|
| C1 | `ci.yml` on open-nvr — server + kai-c pytest on every PR | open-nvr | Green checks |
| C2 | `ci.yml` on ai-adapter — adapter tests + conformance on every PR | ai-adapter | Green checks |

### Phase 5 — Release

| # | Slice | Repo | Output |
|---|-------|------|--------|
| V1 | Reset every `pyproject.toml` to 0.1.0; add release-notes draft | both | Versions match the release |
| V2 | Tag `sdk-v0.1.0` (manual); watch publish workflow ship TestPyPI → PyPI | ai-adapter | First public install of opennvr-adapter-sdk |
| V3 | Tag `v0.1.0` on both; cut GitHub Releases | both | The release itself |

### Phase 6 — Launch

| # | Slice | Output |
|---|-------|--------|
| L1 | Record the 30-second demo GIF (during B0.3) | demo.gif in README |
| L2 | Set up Discord, GitHub Discussions, seed threads | community in place before announcement |
| L3 | Write Show HN + reddit posts; submit Awesome-Selfhosted PR | drafts ready |
| L4 | Coordinated launch (HN + /r/homelab + /r/selfhosted, same hour); pitch creator outreach | the actual launch |

---

## 10. Timeline (honest estimate)

| Phase | Slices | Est. days |
|-------|--------|-----------|
| Phase 0 — Blockers | 4 | 3–4 |
| Phase 1 — Examples (LPR, doorbell, package, HA) | 4 | 10–14 |
| Phase 2 — Default boot | 3 | 3–4 |
| Phase 3 — Docs + scrub | 5 | 4–5 |
| Phase 4 — CI | 2 | 2 |
| Phase 5 — Release | 3 | 1–2 |
| Phase 6 — Launch | 4 | 2–3 |

**Total: ~25–34 working days, i.e. 5–7 calendar weeks.**

Examples dominate the budget. Two ways to manage that:

- **(a) Accept the full timeline** — all 4 examples in v0.1. Strongest launch.
  *(This is the default unless you say otherwise.)*
- **(b) Stagger** — LPR + Smart Doorbell ship in v0.1; Package Delivery +
  HA Relay ship in v0.1.1 two weeks later. Faster initial launch,
  momentum-keeping follow-up.

---

## 11. What I recommend doing first

**Start with B0.1 (pytest fix).** Rationale:

- Smallest blocker. ~½ day.
- Unblocks `pytest` green on fresh clone — a foundational claim the README will make.
- Once it's done, B0.2 (lifespan migration) is the natural next slice in the same area of code.
- B0.3 (fresh-clone smoke test) needs B0.1 + B0.2 done first, because otherwise we'd just be documenting the failures we already know about.

If you approve this plan, my next message starts the B0.1 slice.

---

## 12. Open questions before kicking off

1. **Timeline option (a) or (b)?** Default is (a) — all 4 examples in v0.1.
2. **Discord — do you want me to draft the rules + initial channel
   structure as part of L2, or is community ops something you'd rather
   own directly?**
3. **YouTube outreach (R10c) — do you have any creator relationships
   already, or are we cold-pitching?**
