# Go-to-Market Plan

Internal document — not linked from public docs. Keep this updated as
the launch executes; the playbook only works if it stays honest about
what actually happened.

## Audience map

Three distinct audiences with different motivations, different channels,
and different conversion shapes. Don't try to address them all with one
piece of content.

| Audience | Motivation | Decision-maker | Conversion ask |
|---|---|---|---|
| **Homelab / dev** | Curiosity, weekend project, replacing existing setup | Themselves | `docker compose up` |
| **Procurement / SMB IT** | Compliance, audit-readiness, FCC Covered List substitution | IT director + CISO | Pilot deployment, then commercial-support conversation |
| **Defence / critical-infra / academic** | Architectural credibility, tactical AI sovereignty, paper-citable foundation | Programme lead, CIO, security officer | Architectural review → procurement → sponsored development |

The homelab funnel converts on demos and ease-of-install. The procurement
funnel converts on compliance evidence and reference customers. The
defence funnel converts on the paper, the architectural review, and the
named team behind it. Different content for each — collapsing them
loses all three.

---

## Funnel stage 1 — awareness

### Homelab / dev channels

Order by impact (highest first):

1. **Hacker News Show HN.** Single largest acquisition event for a
   technical project. See [`LAUNCH_COPY.md`](LAUNCH_COPY.md) for the
   draft post. Submit Tue–Thu 9–11am PT. Be in the thread for the first
   two hours.
2. **selfh.st newsletter.** Homelab-focused, weekly cadence, well-read
   by the target audience. Submit a week *before* HN (warm-up signal
   for HN commenters who do due diligence).
3. **r/selfhosted.** Submit timed with selfh.st (similar audience,
   different surface). Format: "Released — OpenNVR v0.1" with the
   camera-agent demo as the lead image.
4. **r/homelab.** Similar to r/selfhosted but slightly different
   user composition. Coordinate timing so you're not double-posting
   the same hour.
5. **r/homeassistant.** Lead with the `home-assistant-relay` example
   and the camera-agent demo. This audience is the closest peer to
   Frigate's; they'll come ready to compare.
6. **r/computervision and r/MachineLearning.** Lead with the adapter
   SDK (~30 lines for a new capability) and the ByteTrack adapter as
   a worked example. These audiences care about the platform, not the
   product.
7. **awesome-selfhosted, awesome-homelab, awesome-rtsp.** PRs to add
   OpenNVR. These take days to weeks to merge but the long-tail SEO
   compounds.
8. **Twitter / Mastodon thread.** Time with HN. Lead with the camera-
   agent demo video, link the HN thread, end with the repo URL.
9. **Hacker News follow-up posts.** "I built an open-source NVR with a
   voice agent — here's what I learned about ___" style retrospective
   posts at the one-month and three-month marks. Different audience
   than Show HN.

### Procurement / SMB IT channels

Slower, deeper. Most of these are reach-out, not broadcast.

1. **r/cybersecurity, r/netsec, r/sysadmin.** Lead with the security
   posture (no-default-password, strong-secret validator, end-to-end
   audit) and the COMPLIANCE.md mapping. Don't lead with the camera-
   agent — wrong audience.
2. **selfh.st feature spotlight.** Long-form version of the blurb (see
   LAUNCH_COPY.md) submitted ~2 weeks after the short blurb.
3. **LinkedIn long-form.** Lead with the Verkada-breach framing and
   the paper. LinkedIn is where IT directors actually read this stuff.
4. **HN follow-up post on the compliance / audit angle.** A separate
   submission 4–6 weeks after the Show HN, framed around an incident
   like "what I learned about IP-camera supply chain by writing an
   architectural paper." Avoids the "this is just an ad" pattern HN
   penalises.
5. **MSP / IT consultancy partnerships.** Warm intros via existing
   MSP-adjacent contacts (community connections, conference
   acquaintances, prior client relationships). Avoid cold-email — the
   pitch lands poorly to inboxes that don't know us. The shape of the
   partnership: OpenNVR provides the architectural backstop, the
   adapter SDK, and the incident-escalation path; the MSP provides
   local deployment hands, customer relationships, and ongoing
   day-two operations. Clear division of value lands better than
   "AGPL alternative to Verkada" framing.

### Defence / critical-infra / academic channels

Direct outreach, almost entirely warm. Don't broadcast — find people
already in your network or one degree away.

1. **Direct outreach to the user's existing government / defence
   contacts.** Send the paper + GOVERNMENT_DEPLOYMENT.md + a short
   personal note explaining what changed since the last conversation.
   Don't send the HN thread — wrong shape.
2. **Academic citation push.** Submit a short systems paper to PerCom,
   IoTDI, SecureComm, or USENIX Security workshops describing OpenNVR
   as a system artefact. Goal isn't acceptance to the top venue —
   it's getting another DOI to cross-link with the architecture paper.
3. **Sector publications.** *Cybersecurity Dive*, *GovInfoSecurity*,
   *FedScoop*, *Critical Infrastructure Protection Report*. Pitch the
   Covered List substitution angle. Most will take a contributed
   article over a press release.
4. **Industry working groups.** ISA-99 / IEC 62443 working group on
   industrial cybersecurity. AWWA water-sector cybersecurity
   committee. NERC CIP standards working groups. These are slow but
   high-signal — being a participant who happens to also have an open-
   source reference implementation is a different relationship than
   being a vendor pitching one.
5. **University CSE departments doing surveillance / IoT security
   research.** Send the paper + offer to host a guest talk or be the
   reference platform for their next system class. Free researchers
   build interesting things on top.
6. **DEF CON / Black Hat IoT village + adjacent hardware-security
   meetups.** Not a sales channel — a credibility-building channel.
   Present the architecture, get challenged by smart adversaries, fix
   what they find.

---

## Funnel stage 2 — consideration

This is where the docs landed last week did most of the work:

- [`README.md`](../../README.md) hero — addresses all three audiences in
  concentric paragraphs.
- [`COMPLIANCE.md`](../COMPLIANCE.md) — paper-to-code mapping for the
  procurement audience.
- [`GOVERNMENT_DEPLOYMENT.md`](../GOVERNMENT_DEPLOYMENT.md) — printable
  one-pager + operational-sovereignty section for the defence audience.
- [`USE_CASES.md`](../USE_CASES.md) — industry segmentation for the
  prospect self-identification path.
- [`COMPARISONS.md`](../COMPARISONS.md) — honest evaluation against
  Frigate / ZoneMinder / Shinobi / Viseron / Verkada.
- [`ROADMAP.md`](../ROADMAP.md) — what's shipped vs what's coming, for
  procurement signing and contributors deciding where to invest.
- [`SUPPORT.md`](../SUPPORT.md) — community vs commercial paths,
  one-click clarity for both.

Gaps still worth filling, in priority order:

1. **Demo video.** 30-second screen capture of the camera-agent in
   action. The HN post, Twitter thread, and selfh.st blurb all depend
   on this. Single highest-leverage missing artefact.
2. **First case study.** Even an anonymised pilot deployment (one
   home-business or one small municipality) materially de-risks the
   procurement conversation. Aim for one by month 2 post-launch.
3. **Branded landing page.** GitHub README is fine for technical
   visitors; non-technical procurement visitors expect a website.
   Optional but increasingly worth doing once the docs settle.

---

## Funnel stage 3 — conversion

For homelab / dev: the 5-minute install path is the conversion. Already
shipped.

For procurement: the conversion is a pilot deployment. Path:

1. They read GOVERNMENT_DEPLOYMENT.md.
2. Run Tier 0 install on a test rack.
3. Layer one or two cameras for a week.
4. Hand the audit log + COMPLIANCE.md to their security officer.
5. Either deploy at scale (community track) or open a commercial-support
   conversation.

For defence / critical-infra: the conversion is sponsored development.
Path:

1. Architectural review (commercial-support engagement).
2. Pilot deployment with operator-customised adapters.
3. Sponsored development of the missing capabilities (federated AI,
   TelemetrySource, sector-specific adapters).
4. Steady-state engagement.

---

## Funnel stage 4 — advocacy

Once we have users, the goal is for them to bring others. Two
mechanisms:

### Referenceability
First three deployments (one per audience) get the white-glove
attention: stable platform, fast bug-fix turnaround, helpful
documentation, and (with consent) a case-study writeup at month 3 or 6.
Reference customers are worth their weight in gold for procurement
conversations later.

### Contributor enablement
Adapter template scaffold + clear ROADMAP make it easy for the
community to contribute. Each merged community adapter is a marketing
moment — "OpenNVR now supports X, contributed by Y" feeds back into
the awareness channels.

---

## Timing

Recommended sequence assuming v0.1 ships next month:

Hard gate: **demo video shipped (edited + captioned + hosted)** before
the Show HN slot. Show HN without it is a self-inflicted wound on the
camera-agent — the most novel thing v0.1 ships. Budget 1–2 weeks
between "recorded" and "shipped" for actual editing time.

| Week | Activity |
|---|---|
| 0 (launch week) | v0.1.0 tagged. Pre-built images on GHCR. README + docs merged. Demo video *recorded* (raw footage). |
| 1 | selfh.st short blurb submitted. PRs opened to awesome-* lists. Demo video editing in progress. |
| 2 | r/selfhosted + r/homelab posts. Demo video shipped (this is the hard gate before HN). |
| 3 | Twitter / Mastodon thread coordinated with demo-video release. r/homeassistant post. |
| 4–5 | **Hacker News Show HN.** Demo video embedded in the post. Maintainer in the thread for the first two hours. |
| 6 | r/computervision + r/MachineLearning follow-on posts on the adapter SDK. Direct outreach to user's existing gov/defence network. |
| 7–8 | r/cybersecurity post + LinkedIn long-form on the compliance angle. selfh.st feature spotlight. |
| 9–10 | First case study draft (with consent). Sector-publication pitches. |
| 11–14 | One follow-up HN post on the compliance / audit angle. First academic-venue submission. |
| 14+ | Steady cadence — release notes for v0.1.x patches, contributor highlights, sector-specific blog posts. |

Slipping any step doesn't break the plan — it just shifts the cadence.
What matters is not skipping the consideration-layer docs (already
shipped) and not rushing the academic / defence outreach (those
require warm relationships, not broadcast).

---

## Metrics worth tracking

Not vanity metrics — actionable ones.

Two columns — baseline = "healthy v0.1 launch", stretch = "exceptionally
well-received v0.1 launch." Hitting baseline at month 3 is a real
outcome we should celebrate, not a failure to be patched.

| Metric | What it tells us | Baseline (month 3) | Stretch (month 3) |
|---|---|---|---|
| GitHub stars | Awareness signal (noisy but cheap) | 200+ | 1,000+ |
| Repo clones | More accurate awareness signal | 1,000+ unique | 5,000+ unique |
| Docker image pulls | Real "tried it" signal | 300+ pulls of the core image | 1,000+ |
| GitHub Discussions activity | Engagement depth | 15+ threads with maintainer + community responses | 50+ |
| Discord / Matrix membership (once set up) | Synchronous community | 50+ members | 200+ |
| Commercial-support inbound | Funnel signal for paid track | 1-2 scoping conversations | 5+ |
| Paper citations | Academic adoption (slow signal) | 0 (this is a year-1 metric) | 1+ |
| Reddit / HN comment quality | Community health | Mostly technical / on-topic, not "another NVR" dismissals | Same |

If after three months the homelab funnel is healthy but the procurement
funnel is empty, that's signal we need to invest harder in the
procurement outreach. If it's the reverse, the homelab funnel needs
demo / UX polish that v0.2 should prioritise.

---

## Don'ts

- **Don't pay for awareness.** Paid acquisition in this category
  signals desperation and attracts the wrong audience. Earned
  awareness (HN, awesome lists, sector publications) is much higher
  quality.
- **Don't pitch as a Frigate killer.** The Frigate audience knows what
  it has and most of them are happy. Different category, different
  pitch.
- **Don't oversell defence capabilities at v0.1.** Hardware tamper
  detection, FIPS conformance, Common Criteria — these are roadmap,
  not shipped. Lead with what's true, name what's coming, deliver
  in public.
- **Don't sandbag the camera-agent.** It's the most novel thing
  v0.1 ships. Lead with it on the Twitter / Mastodon / video channels
  where novelty drives engagement. Just don't make it the whole pitch
  on the compliance-audience surfaces.
- **Don't engage hostile commenters.** Some HN / Reddit threads will
  attract "this is just Frigate with extra steps" or "AGPL is
  contagious" objections. Acknowledge briefly, link to the right
  page, move on. Long fights in public threads damage the launch.

---

## Owners

This doc is internal — owners aren't listed because the team
composition is private. When external coordination is needed,
delegate-by-name in this doc with a date and review on each entry.
