<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Growing to many use cases — one composable agent, not many agents

Storyteller, watchman, door concierge, PPE/no-helmet compliance, no-entry-zone
intrusion, appointment checker… these look like different products. They aren't.
**They're the same engine with different configuration.** Writing a separate
hardcoded agent per use case does not scale — and you don't need to.

## The one idea

> **One agent engine. Many *recipes* (config). Three extension points.**
> Use cases are *declared*, not coded. Users configure their own; they don't fork.

## Every use case decomposes into the same primitives

| Primitive | What it is | Examples |
|-----------|-----------|----------|
| **Persona** | who it is + how it talks | "Narrator", "Watchman", a calm concierge |
| **Senses** | which perception adapters are active | detect, VQA, face, **PPE/helmet**, fire, fall, pose |
| **Rules** | predicates over what's seen | `person AND zone=no_entry AND 18:00–06:00 → alarm` |
| **Actions** | effects when a rule fires | notify, **call owner**, log, open gate, MQTT/Home-Assistant |
| **Schedules** | time-driven jobs | "every 8am, tell the last-24h story" |
| **Zones** | regions of interest per camera | "no-entry area", "loading dock", "door" |

Map the asks to primitives — notice they're all combinations of the *same* parts:

| Use case | Persona | Senses | Rule | Action | Schedule/Zone |
|----------|---------|--------|------|--------|---------------|
| **Storyteller (24h)** | narrator | recent-events | — | speak/notify | daily 8am |
| **Watchman (Q&A)** | watchman | detect/VQA/face | — | speak | — |
| **Door concierge** | concierge | face + VQA | person at door | collect info → **call owner** → permit/deny → log | door zone |
| **PPE compliance** | safety | helmet/PPE detector | `person AND helmet=absent` | alarm + log | site zone |
| **No-entry zone** | guard | detect + zone | `person IN zone AND after-hours` | alarm + notify | no-entry zone |
| **Appointment checker** | receptionist | face | known visitor at door | check schedule → greet/deny | door zone |

Almost all of this is **already in the engine**: standing monitors + alarms
(rules), scheduled reports (storyteller), notifications/webhooks (actions),
persona, and the adapter contract (senses). The gaps are: a proper **rule/
predicate language**, **zones/ROI**, **attribute senses** (helmet/PPE), and a
**recipe config** that ties it together.

## Three extension points (how capability grows)

You never edit the core to add a use case. You extend at one of three seams:

1. **Adapters = new senses.** Helmet/PPE, fire, fall, pose, ANPR — each is a new
   adapter under the AI Adapter Contract. A rule just references it by task name.
2. **Flows/skills = new multi-step behaviors.** The door concierge ("greet →
   collect → call owner → permit/deny → log") is a *flow* (Pipecat Flows), not a
   tweak to the base prompt.
3. **Actions/integrations = new effects.** Call, open a gate, push to Home
   Assistant/MQTT, file an incident — pluggable action handlers (today: webhooks
   + the documented call hook; tomorrow: the ROS2/MAVLink bridge for robots).

## The config: an agent *recipe* (the file users write)

A use case = a declarative recipe (YAML), not code. Sketch of the schema we'd
formalize:

```yaml
persona:   { name: Watchman, voice: male, tone: calm }
senses:    [detect_objects, describe_camera, recognize_faces]   # which adapters
zones:
  no_entry: { camera: cam1, polygon: [[0.1,0.2],[0.5,0.2],[0.5,0.8],[0.1,0.8]] }
rules:
  - when: { object: person, zone: no_entry, time: "18:00-06:00" }
    then: [ alarm(critical), notify(owner) ]
  - when: { object: person, attribute: { helmet: absent } }
    then: [ alarm("PPE violation"), log ]
schedules:
  - at: "08:00"  then: report(query="what happened in the last 24 hours")
actions:
  owner: { type: webhook, url: "..." }   # or call / mqtt / ha
```

Ship a **recipe gallery** (`watchman.yml`, `ppe-compliance.yml`,
`door-concierge.yml`, `storyteller.yml`) users copy and tweak. This is the same
"applications ship on top" idea the repo already has — but **declarative config
instead of forked example code**, so a non-developer can author a use case.

## Do we run multiple agents?

Mostly **no**. One engine runs **many rules and personas concurrently** (the
monitor/alarm engine already polls many rules in parallel) — a single deployment
can be watchman + PPE-checker + storyteller at once. Run multiple *instances*
only for **isolation or scale** (separate sites, separate tenants, GPU sharding)
— that's deployment topology, not new code.

## The growth roadmap (to make this real)

1. **Rule/predicate engine** — boolean conditions over `object` + `attribute` +
   `zone` + `time` + `count`/dwell (generalises today's target+time-window),
   each tagged with a **severity tier** (`info`/`warning`/`critical`).
2. **Critical-incident escalation profile** — the shared fail-safe path for the
   life-safety tier (ring → call → notify-all → record → actuate), polled
   fastest, never throttled, with confirm + ack.
3. **Zones / regions of interest** per camera (no-entry, dock, door).
4. **Attribute/safety senses** — **fire/smoke**, PPE/helmet, pose/fall, ANPR
   adapters (new adapters, no core change).
5. **Recipe schema + loader** — the YAML above, validated, hot-reloadable.
6. **Recipe gallery** — copy-to-start templates per vertical.
7. **Flows** for the multi-step ones (concierge, footage search, enrollment).
8. **Action plugins** — call, gate, MQTT/HA, incident export.

## Common & impactful use-case catalog (the recipe-gallery backlog)

Each is a *recipe* (persona + senses + rule + action) on the one engine — not a
new agent. ⭐ = ship-first (common + high value). Sensitive ones flagged 🔒.

### ⚠️ Critical incidents (life-safety tier — its own category)

Fire, smoke, and similar events aren't "just another rule" — they're a distinct
**severity tier** where seconds matter and the response is *immediate
escalation*. Group them as **critical incidents** with a shared escalation
profile, the highest reliability, and priority over everything else.

| Incident | Senses | Trigger | Escalation |
|----------|--------|---------|------------|
| ⭐ **Fire / smoke** | fire-smoke adapter | smoke or flame detected | ring siren + **call** fire service + notify all + record |
| Gas / CO | sensor integration | threshold exceeded | alarm + call + evacuate notice |
| Flood / water leak | VQA/seg or sensor | water present | alarm + notify + (valve) shutoff |
| Person down / collapse | pose/fall adapter | person down, not moving | alarm + **call** contact/medical |
| Forced entry / glass-break | detect + audio | break event | alarm + call + record |
| 🔒 Weapon / armed intruder | detect/weapon adapter | weapon visible | alarm + call security + lockdown signal |

**The "critical" action profile** (one shared escalation path): immediate
ringing alarm → **call** the configured emergency contact (the documented call
hook) → fan out to *all* notification channels → start recording a clip → and,
where wired, trigger actuation (gate lockdown, valve shutoff). It must be
**fail-safe**: critical rules are polled fastest, never throttled by background
tasks, deduplicated, and require an explicit human acknowledge to clear.

Design implications for the rule schema:
- A **severity tier**: `info` / `warning` / **`critical`** (the alarm engine
  already rings for critical — formalise the *escalation profile* on top).
- **High-recall detectors** for life-safety (a false alarm beats a miss), with
  N-consecutive-frame confirmation to keep false positives down, plus a clear
  ack/silence flow.
- These recipes (fire/smoke especially) are what justify the product for
  **buildings, factories, schools, and care homes** — list them first.

Fire/smoke is a **new sense** (a vision smoke/flame detection adapter under the
AI Adapter Contract) + the **critical escalation action** — no core change.

**Home & consumer** (drives adoption — high volume, low friction)

| Use case | Senses | Trigger | Action |
|----------|--------|---------|--------|
| ⭐ Doorbell concierge | face, VQA, detect | person/package at door | announce, notify, deliver instructions |
| ⭐ Package detection & theft | detect | parcel appears / removed by stranger | notify, log clip |
| ⭐ After-hours intrusion | detect, zone | person outside 18:00–06:00 | alarm, notify |
| ⭐ Daily storyteller / digest | recent-events | schedule 8am | speak/notify "what happened in 24h" |
| Pet monitor | detect | dog on couch / pet escaped yard | notify |
| Garage/door left open | detect | door open > N min | remind |
| 🔒 Elderly fall detection | pose/fall adapter | person down, not moving | alarm, call contact |
| 🔒 Child safety zone | detect, zone | child near pool/stairs | alarm |

**Industrial safety & compliance** (highest willingness to pay)

| Use case | Senses | Trigger | Action |
|----------|--------|---------|--------|
| ⭐ PPE — no helmet/vest | PPE adapter | person AND helmet=absent | alarm, log incident |
| ⭐ No-entry / restricted zone | detect, zone | person in zone (after-hours) | alarm, notify |
| Forklift–pedestrian proximity | detect, track | person within X of forklift | alarm |
| Machine-guarding / danger zone | detect, zone, pose | hand/person in danger zone | stop-line signal, alarm |
| ⭐ Fire / smoke | fire adapter | smoke/flame detected | alarm, call, notify |
| Evacuation headcount/muster | detect, count | during drill | live count per zone |
| Spill / leak | VQA/seg adapter | spill present | notify, log |

**Retail & commercial**

| Use case | Senses | Trigger | Action |
|----------|--------|---------|--------|
| ⭐ Occupancy / queue length | detect, count | count > threshold | notify, analytics |
| Loitering | detect, dwell | dwell > N min | notify |
| Slip-and-fall (liability) | pose/fall | fall in store | log clip, notify |
| After-hours intrusion | detect, zone | person after close | alarm |

**Logistics & warehouse**

| Use case | Senses | Trigger | Action |
|----------|--------|---------|--------|
| ⭐ Gate ANPR (license plate) | ANPR adapter | vehicle at gate | log, allow/deny, open gate |
| Loading-dock monitoring | detect | trailer present/absent | notify |
| Entry/exit counting | line-crossing | crossing | count, analytics |

**Healthcare / care, agriculture, perimeter security** (more verticals)

| Use case | Senses | Trigger | Action |
|----------|--------|---------|--------|
| 🔒 Wandering / elopement | face, zone | patient leaves area | alarm, notify staff |
| 🔒 Bed-exit | pose, zone | person exits bed | notify |
| Livestock count / predator intrusion | detect | animal count / predator | notify |
| Perimeter intrusion | detect, zone | person crosses fence line | alarm |
| Abandoned object | detect, dwell | object left unattended | alarm |

### Ship-first set (the gallery's first recipes)
**⚠️ Fire/smoke** (life-safety, every building) · doorbell concierge · package
detection · after-hours intrusion · daily storyteller · PPE helmet · no-entry
zone · occupancy/queue · gate ANPR. These cover life-safety + home + industrial
+ retail + logistics — the main buyer types — with the fewest new adapters
(fire/smoke, PPE, ANPR, fall/pose are the new senses).

### 🔒 Responsible-use note
Face recognition, behavior inference, weapon detection, and worker-monitoring
carry **legal, privacy, and ethical constraints** that vary by jurisdiction
(GDPR, BIPA, workplace law, etc.). Ship these recipes with consent/notice
guidance, make them opt-in, keep the audit trail (KAI-C already logs every
inference), and default to the sovereign posture. The platform's value is that
the data and decisions stay with the operator — design the recipes to honour
that, not undermine it.

## Takeaway
A new use case should be a **recipe file + (maybe) one new adapter or flow** —
never a new codebase. Build the composable engine + the recipe schema + the three
seams, and the long tail of use cases (yours *and* your users') becomes
configuration. That's what turns this from a camera agent into a camera-agent
*platform*.
