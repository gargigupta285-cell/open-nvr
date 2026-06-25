<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later

NORTH-STAR / POSITIONING ONE-PAGER — proposed front door (README).
Lead with this; demote everything else one click deeper.
-->

# OpenNVR

**Your cameras. Your AI. Your hardware. Your control.**

OpenNVR is a self-hosted, security-first AI network video recorder. It started
by fixing the security holes that plague off-the-shelf NVRs, and it answers the
one question every other camera system dodges: *who is watching your footage,
and where does the AI run?* With OpenNVR the answer is always **you, on your own
box** — and if you ever want a bigger brain, you bring your own AI on your own
terms.

> Frigate gives you great local detection. The cloud cams give you a slick app.
> Nobody gives you **sovereign by default + bring-your-own-AI**. That's us.

---

## Talk to your cameras in 60 seconds

```bash
git clone https://github.com/open-nvr/open-nvr && cd open-nvr
cp .env.example .env                       # set one secret
docker compose -f docker-compose.tier0.yml \
               -f docker-compose.camera-agent.yml \
               --profile camera-agent-lite up -d
# open http://localhost:9100/demo and TYPE: "how many people are at the door?"
```

That's **Spotter** — fully local, ~1–2 GB RAM, runs on a normal CPU. No GPU, no
cloud account, no 12 GB download. This is the front door; everything below is
one flag away from here.

---

## Pick your edition — the whole product is two dials

You choose **how much you trust the AI** (run it on your box vs. bring your own
cloud key) and **how much hardware you have** (laptop CPU vs. GPU box). That's
it — pick a cell and go.

| | Lightweight · CPU | More power · GPU |
|---|---|---|
| **Most secure** — AI runs on your box, nothing leaves (`local_only`) | **Spotter** — text chat, detect & count, monitors, alarms · ~1–2 GB | **Sentinel** — hands-free voice + full vision, the flagship sovereign agent |
| **AI of your choice** — bring your own model/key (hybrid) | **Sentinel Cloud** — local vision, cloud brain · ~1–2 GB, most reliable | **Sentinel Cloud + GPU** — local GPU vision + cloud brain |
| **In between** — richer local understanding | **Watch** — Spotter + scene description, visual Q&A, "find the red truck" · ~3–4 GB | |

**Frames never leave your machine in any edition.** Cloud editions send only the
chat text to the provider you chose, and say so out loud (audited, opt-in).
`local_only` is the default and the promise; the cloud doors exist because *your
AI, your choice* is the other half of the mission — not because we blinked on
security.

Full edition + model details: [`examples/camera-agent/EDITIONS_AND_MODELS.md`](examples/camera-agent/EDITIONS_AND_MODELS.md).

---

## Why it exists (the security story)

OpenNVR began as a fix for documented NVR security failures — the things that get
cameras banned from government buildings and leaked onto the open internet. The
posture is the product:

- **No vendor egress by default.** Under `local_only`, the AI sovereignty layer
  refuses any adapter that tries to phone home. Air-gap-clean, NDAA-minded.
- **Every AI call is audited** through the KAI-C connector — you can see exactly
  what ran, on what, and where.
- **You own the models.** Swap, upgrade, or bring your own via the open Adapter
  Contract. No black box, no forced cloud, no per-camera SaaS bill.

This is the moat. Lead with it.

---

## What it can do (examples gallery — opt in, don't get overwhelmed)

These are *recipes that showcase the platform*, not things you must learn to
start. Turn on what you need:

- **Ask your cameras** — "how many cars in the lot?", "is the gate open?",
  "did anyone walk past in the last 30 minutes?"
- **Standing monitors** — "tell me if more than 3 people gather at the entrance."
- **Smart alarms** — person-after-6PM, fire/smoke, custom windows; ringing
  banner; emergency-call hook (documented).
- **Open-vocabulary search** — "find a red truck" with no retraining.
- **Faces & watchlist** — enroll known people, flag unknowns.
- **Hands-free voice persona** — a named assistant (Shailaja / Sidhu) with an
  avatar, for the full Sentinel experience.
- **Scheduled reports & webhooks** — recurring summaries, push to your tools.

Each is a demo of the same engine; none of them is *the* product. The product is
the sovereign platform underneath.

---

## The honest to-do (what still makes it easier to love)

We know the friction. The roadmap is about *removing doors*, not adding rooms:

1. **One repo, one quickstart.** Today the stack spans three repos
   (open-nvr / ai-adapter / kai-c) — the single biggest reason people bounce.
   Consolidate the getting-started path so `clone → up` just works.
2. **Lighter default.** Spotter is the default; the heavy voice stack is opt-in.
   Next: collapse the three voice adapter containers (each ships its own ~2 GB
   PyTorch) into the one combined image the repo already builds.
3. **Bring-your-own-AI, frictionless.** The OpenAI-compatible client already
   lets any provider be the brain; make it a one-line setting in the UI.
4. **Home Assistant / NVR integrations** so it slots into setups people already
   run.

---

## The path: popularity → community → business

The order matters. First make it *trivially runnable and obviously useful*
(Spotter default, one-repo quickstart) so people star, fork, and file issues.
Then the sovereign + bring-your-own-AI angle earns the audience nobody else
serves — privacy-sensitive homes, schools, clinics, small public agencies, and
anyone who can't legally send footage to a vendor cloud. **That** audience is the
business: supported on-prem deployments, a managed control plane for fleets, and
certified hardware bundles — sold on the one thing the incumbents can't offer,
which is that the customer stays in control.

Sovereign by default. Your AI when you want it. That's the whole pitch.
