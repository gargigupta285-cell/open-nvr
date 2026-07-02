# Use Cases by Industry

This page is for evaluators trying to figure out whether OpenNVR fits their
environment. Each segment below has a short fit assessment, the concrete
analytics that map to OpenNVR's shipped capabilities, and an honest note
about what you'd build vs what you'd configure.

The architecture itself is industry-agnostic — what changes per segment is
which adapters you compose and what predicates you write on top of them.
The examples gallery ([`examples/README.md`](../examples/README.md)) is the
copy-as-template starting point for any segment-specific app.

Each segment is structured the same way: what about the architecture matches its threat model or operational reality, what's available straight from a standard stack install plus shipped adapters, what requires writing a predicate (~50 lines of Python following an example app) or authoring a custom adapter (~30 lines following the template), and the gaps v0.1 will hit. A note on what "custom adapter" means across this page: the SDK wrapping itself is ~30 lines per the template scaffold; the *model* — PPE compliance, weapon detection, fall detection, drone classifiers — is an ML-engineering effort proportional to the false-positive-rate budget the operator can tolerate. For mission-critical FPR thresholds, expect tens of thousands of site-specific annotated frames, not a weekend. The [`SUPPORT.md`](SUPPORT.md) commercial-support track exists for deployments that need this model work delivered under contract.

---

## Critical infrastructure (power, water, telecom, oil & gas)

**Why OpenNVR fits.** Substations, pumping stations, cell towers, and pipeline
right-of-way deployments share the same posture: physically distributed,
operationally critical, regulatorily scrutinised, and increasingly required
to operate without trust in third-party clouds. OpenNVR's offline-first
posture and customer-managed keys map cleanly to NERC CIP, TSA Pipeline
Security Directives, and AWWA water-sector guidance.

**Out-of-the-box.** standard stack install gives you isolated camera network →
encrypted middleware → audit log. YOLOv8 detection on perimeter and yard
cameras. License-plate logging at access roads via the `fast-plate-ocr`
adapter and the `license-plate-recognition` example.

**Configure / extend.** Equipment-tamper detection trained on your specific
cabinet imagery, drone-detection models tuned to your local airspace
baseline, and fence-line analytics with site-specific intrusion patterns are
all custom-adapter work. Each is roughly one weekend of model fine-tuning
plus the ~30-line SDK wrapper.

**Caveats.** No native SCADA integration in v0.1; alerts flow to NATS and you bridge to your SCADA stack via the `alerts-subscriber` example. Drone detection in v0.1 is custom-visual-classifier work (community models exist, fine-tuning to your local airspace baseline is the ML-engineering effort); acoustic drone detection becomes available with the v0.2 audio-events adapter category.

---

## Defense and military bases

**Why OpenNVR fits.** Two things matter most here: (1) the FCC Covered List
substitution argument (Hikvision, Dahua, certain Hytera equipment is
restricted for defence use), and (2) the operational sensitivity of tactical
AI — perimeter-classifier weights, behavioural-anomaly thresholds, and
recognition models cannot leave the base. OpenNVR's AI sovereignty
enforcement is the mechanism that keeps them inside.

**Out-of-the-box.** standard stack install on isolated VLANs with no internet
default-gateway. InsightFace face recognition with operator-managed face DB.
End-to-end correlation ID for incident reconstruction. Append-only audit
log.

**Configure / extend.** Vehicle / uniform / equipment classifiers trained on
your specific imagery. Behavioural-anomaly detection with rules your
operations team — not a vendor — defines. UAV / counter-UAV adapters
(visual + RF where applicable). All custom-adapter work; Apache-2.0 SDK
licence permits adapters under any compatible licence including classified
or proprietary.

**Caveats.** Hardware tamper detection on the OpenNVR servers themselves
(TPM attestation, secure boot integration) is on the v0.3 roadmap; for v0.1
this is an operator-control matter (locked racks, physical perimeter).

---

## Government facilities and public sector

**Why OpenNVR fits.** Municipalities, courts, social-services offices, and
public-safety facilities sit at the intersection of FCC Covered List
restrictions, FERPA (where K-12 services are involved), HIPAA-adjacent
requirements (where social services or jails are involved), and increasingly
strict procurement rules around vendor-cloud video. OpenNVR is procurement-
defensible (see [`COMPLIANCE.md`](COMPLIANCE.md)) and architecturally
local-only by default.

**Out-of-the-box.** Web UI for non-technical staff (front-desk
visibility), per-camera retention policies for records-office compliance,
RTSPS / HLS-TLS / WebRTC-TLS for browser access without certificate
warnings (with your own CA), and an audit log that produces records
defensible in FOIA / RTI contexts.

**Configure / extend.** Visitor-flow analytics, tailgating detection,
package-screening with custom item lists, badge correlation via the
`alerts-subscriber` example bridged to your access-control system.
Mostly configuration of existing examples.

**Caveats.** Active Directory / SAML SSO integration for staff
authentication is planned for v0.2; v0.1 uses local accounts.

---

## Healthcare (hospitals, clinics, long-term care)

**Why OpenNVR fits.** HIPAA's technical safeguards rules require encryption
of PHI in transit and at rest, audit controls, and access management.
OpenNVR's `local_only` deployment mode means video — which is PHI when it
captures patients — never leaves your network. Customer-managed encryption
keys mean your security officer, not a vendor, holds the keys. The audit
chain produces evidence for OCR investigations or breach assessments.

**Out-of-the-box.** TLS on every viewer transport, role-based access
control, audit log per inference. Patient-fall detection via custom
pose-estimation adapter (template ships, model is yours to choose — MediaPipe
Pose, MMPose, YOLOv8-pose all work).

**Configure / extend.** Restricted-area logic (medication rooms, NICU
corridors, behavioural-health units) is per-zone predicate configuration
following the `intrusion-detection` example pattern. Wandering-patient
detection for memory-care facilities, equipment-tracking for crash carts
or infusion pumps — all custom-adapter work with off-the-shelf models.

**Caveats.** HL7 / FHIR integration isn't built in; bridges to clinical
systems flow through the `alerts-subscriber` example. v0.1 doesn't ship
a fall-detection adapter (pose-estimation adapter is on the v0.2
roadmap), so fall use cases require custom adapter authoring today —
and the model side is real ML work (temporal smoothing to avoid firing
on bending or sitting), not just an SDK wrap.

---

## Education (K-12 and higher education)

**Why OpenNVR fits.** Two pressures intersect here: FERPA's restrictions on
student data sharing make vendor-cloud video architecturally non-compliant
in many districts, and active-shooter / weapon-detection requirements after
recent incidents demand AI capabilities that schools want to control
themselves (over-firing alerts, false positives, demographic bias are all
school-specific tuning concerns).

**Out-of-the-box.** standard stack install on the school's own server. YOLOv8
detection on hallway / entry cameras. After-hours intrusion alerting via the
`intrusion-detection` example with school schedules. Per-camera retention
matching state record-keeping requirements.

**Configure / extend.** Weapon-detection classifier (community models exist;
operator chooses and retrains as adversaries adapt). Bullying / fight
detection via behavioural-anomaly models. Bus-arrival analytics on parking
lot cameras. Most of this is custom-adapter work because schools have
strong reasons not to share training imagery with vendors.

**Caveats.** Mass-notification integration (e.g., Crisis Alert) isn't
built in; v0.1 routes through `alerts-subscriber` to your existing
notification system.

---

## Industrial / manufacturing / process plants

**Why OpenNVR fits.** OT environments are typically air-gapped or
near-air-gapped from corporate networks — exactly the deployment posture
OpenNVR is designed for. PPE-compliance pressure (driven by insurance, not
just regulation) is increasingly making AI-on-camera mandatory. Compliance
documentation needs to survive an audit, which the audit chain provides.

**Out-of-the-box.** YOLOv8 detection with site-specific class filtering.
ByteTrack for persistent track IDs on moving equipment. License-plate
logging at gate and shipping cameras.

**Configure / extend.** PPE-compliance models (hard hat / vest / safety
glasses / hearing protection) — community models exist, site fine-tuning
recommended. Lockout-tagout zone monitoring via per-zone predicates.
Forklift-near-pedestrian alerting. Equipment-behaviour anomaly via
custom adapters.

**Caveats.** No direct MES / SCADA / historian integration in v0.1; flow
alerts via `alerts-subscriber`. Time-sync precision for fault correlation
with PLC events is best-effort RTSP-derived; v0.2 plans tighter clock
discipline.

---

## Logistics / warehousing / distribution centres

**Why OpenNVR fits.** Loss-prevention budgets are real, dock-door incident
investigation needs reliable evidence, and forklift-related injuries
create insurance pressure for AI-driven safety analytics. OpenNVR's
ByteTrack adapter unlocks the per-track state machines that "did this
forklift just enter the pedestrian zone?" actually requires.

**Out-of-the-box.** YOLOv8 + ByteTrack for tracked detection. The
`package-delivery` example demonstrates the per-track state-machine pattern
(arrival → linger → disappearance) — drop in your own predicate and it
becomes loading-bay-arrival-or-theft.

**Configure / extend.** Pallet / SKU recognition via custom OCR adapter
(fast-plate-ocr can be adapted, or use PaddleOCR via a new adapter). Worker
ergonomics / lifting-form analytics via pose adapters. Conveyor-jam
detection via behaviour-anomaly models.

**Caveats.** WMS integration is `alerts-subscriber` work — no out-of-the-box
WMS adapter. Sort-line speed measurements need higher per-camera FPS than
default standard stack inference loops; tune `inference_interval` per camera.

---

## Retail loss prevention / store operations

**Why OpenNVR fits.** Retail LP has a specific buyer (LP manager / asset
protection director) with budget and ROI math (shrinkage reduction).
Customer-flow analytics + dwell-time + queue-length tracking are
operationally valuable. Privacy concerns around demographic / face
recognition push retailers toward architectures where they control the
inference pipeline — exactly what OpenNVR offers.

**Out-of-the-box.** YOLOv8 person detection + ByteTrack tracking gives you
the foundation for dwell-time and queue-length analytics. The `loitering-
detection` example is the pattern for "person in this zone longer than N
seconds" alerts.

**Configure / extend.** Concealment-behaviour models (shopping cart
manipulation, bag staging) via custom adapters. Self-checkout
loss-prevention via custom predicates over YOLOv8 detections. Demographic
analytics — if your jurisdiction permits — via custom adapters, all
processing local.

**Caveats.** POS-system integration (matching detection events to
transactions) isn't built in; bridge via `alerts-subscriber`. v0.1 doesn't
ship a re-identification adapter (track-across-cameras), so "did this
person come back later?" requires custom work.

---

## Cannabis (legal-market dispensaries and cultivation)

**Why OpenNVR fits.** State-mandated video retention requirements (typically
30–90 days, full-coverage of cultivation / packaging / vault zones) make
"vendor-locked, vendor-cloud, vendor-priced" the wrong architecture. Most
states' rules explicitly require operator-controlled retention with audit
logs. OpenNVR's append-only audit log plus local retention satisfies the
regulatory shape directly.

**Out-of-the-box.** standard stack install with per-camera 24×7 recording at the
retention period your state requires. Audit log for every operator access
to the video. License-plate logging at vehicle access points. Compliance-
record export via the `alerts-subscriber` pattern.

**Configure / extend.** Inventory-zone access logging (badge correlation +
camera ID). Trim-room compliance recording. Vehicle-arrival logging for
chain-of-custody. Most of this is configuration of existing examples.

**Caveats.** State-specific Metrc / BioTrack / similar seed-to-sale
integrations are operator work; the data shape is in the audit log and
can be exported. v0.1 doesn't ship a state-template configurator.

---

## Construction sites and infrastructure projects

**Why OpenNVR fits.** PPE compliance is insurance-driven (real budget,
clear ROI), equipment theft prevention has direct loss-avoidance math, and
sites are typically air-gapped or near-air-gapped from corporate IT
(OpenNVR's offline-first posture is exactly right). Time-lapse + safety
monitoring stack on the same hardware.

**Out-of-the-box.** YOLOv8 detection for personnel and vehicle counts.
ByteTrack for persistent IDs across the working day. After-hours intrusion
via `intrusion-detection` example.

**Configure / extend.** PPE-compliance models — hard hat / high-vis vest /
safety harness — via custom adapters (community models available). Heavy-
equipment proximity alerting. Concrete-pour event detection. Stolen-tool
detection via the `package-delivery` example's state-machine pattern,
inverted.

**Caveats.** Sites with intermittent network connectivity need local
recording with delayed alert sync — v0.1 records locally fine, but
NATS-based alert fan-out assumes the broker is reachable. Solar / battery
deployments work but should size disk capacity carefully.

---

## Aviation, maritime, and port security

**Why OpenNVR fits.** Regulatory environment (TSA, USCG, IMO, ICAO) drives
audit-grade record-keeping. Insider-threat models are taken seriously. AI
sovereignty matters because passenger / cargo / personnel imagery is
operationally sensitive. The FCC Covered List substitution argument applies
here too.

**Out-of-the-box.** standard stack install with isolated camera networks per
terminal / berth. License-plate logging at vehicle gates and cargo lanes.
YOLOv8 for personnel and vehicle counts.

**Configure / extend.** Runway / taxiway incursion detection via custom
geofence predicates. Vessel-class identification at port-of-entry via
custom classifiers. Cargo-handling event logging via per-zone state
machines.

**Caveats.** TSA / USCG-specific compliance evidence templates aren't
shipped; commercial-support engagement is the path for regulated
deployments where the auditor wants a turnkey evidence pack.

---

## Smart city / municipal surveillance

**Why OpenNVR fits.** Cities buying surveillance face increasing public-
sector pressure around vendor-cloud and FCC Covered List substitution.
Public-records requests (FOIA / RTI / state equivalents) demand evidence
of what was recorded and who accessed it — exactly what the audit log
delivers. Privacy advocacy groups (rightly) push back on vendor-controlled
analytics; OpenNVR's local-only posture preempts that conversation.

**Out-of-the-box.** standard stack install per district / precinct / department.
License-plate logging on roadway cameras. Pedestrian / vehicle counts via
YOLOv8.

**Configure / extend.** Traffic-incident detection. Crosswalk-safety
analytics. Crowd-density estimation for events. Public-space behavioural
alerting (use carefully — civil liberties implications). All custom-adapter
work with off-the-shelf models.

**Caveats.** Multi-jurisdiction deployments (city + transit authority +
school district sharing infrastructure) need careful tenant separation
that v0.1 doesn't fully model; commercial-support engagement recommended.

---

## What v0.1 doesn't cover well yet

OpenNVR isn't the right answer for everything. standard stack scales comfortably to about 50 cameras on commodity hardware; beyond that the project supports multi-host deployments but doesn't yet make them easy. Body-worn cameras with frequent connectivity transitions aren't a first-class target — OpenNVR expects fixed RTSP / ONVIF endpoints, and body-cam buffered-upload patterns require glue you'd need to write. Drone and UAV mobile platforms need the geo-aware moving-camera `TelemetrySource` abstraction planned for v0.2. Audio-first deployments work for the speech path (Whisper STT and Piper TTS ship) but audio-event detection — gunshot, glass-break, dog-bark — requires a new adapter category, also planned for v0.2.

For timing on the gaps above see the [Roadmap](ROADMAP.md), and if you want to contribute against them the [Adapter template](https://github.com/open-nvr/ai-adapter/tree/main/templates/adapter-template) is the starting point.

## Don't see your segment?

The architecture is intentionally generic. The shipped examples and
adapters demonstrate the patterns; the SDK and template scaffold mean a
new segment is at most a weekend of work. Open a
[Discussion](https://github.com/open-nvr/open-nvr/discussions) if you'd
like help scoping your use case — happy to point at the closest existing
example pattern.

For commercial deployments where you'd rather not author the analytics
yourself, see [`SUPPORT.md`](SUPPORT.md).
