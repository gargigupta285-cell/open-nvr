<!--
Copyright (c) 2026 OpenNVR
SPDX-License-Identifier: AGPL-3.0-or-later

Vision / positioning doc. Forward-looking direction — not a committed feature
set. Marks clearly what already exists vs. what would need building.
-->

# OpenNVR for edge autonomy — robots, drones & unmanned vehicles

**Thesis:** OpenNVR's real core isn't "recording cameras" — it's a **sovereign
perception-and-decision layer**: streams in → local models → an auditable AI
decides → nothing leaves the machine. A robot, a drone, or a ground/underwater
vehicle is the *same loop* with moving cameras and an actuator on the end. So
OpenNVR can be the **brain that sees and decides on the edge**, riding on top of
a real-time control stack — not the flight controller or motion planner itself.

And the sovereignty pitch is *stronger* here than for fixed NVR: machines that
operate in contested, denied, regulated, or disconnected environments often
**can't** phone home to a vendor cloud. Air-gapped, NDAA-clean, your-own-AI,
audit-on-demand is the requirement, not a nice-to-have.

## The superpower: one brain, many eyes

The thing that makes OpenNVR powerful on a robot is **multi-source fusion**. The
agent isn't tied to one sensor — it can pull from, and reason across, *all of
them at once*:

- the platform's **own onboard cameras** (auto-discovered via the `device:`
  source — laptop/Pi/USB/`/dev/video`),
- **remote streams** the robot can reach — `rtsp://` / `http://` snapshots from
  other cameras, a docked drone, a fixed camera on the site, a teammate robot,
- additional **sensors as adapters** (thermal, depth, LiDAR-derived imagery,
  open-vocabulary detectors) through the same AI Adapter Contract.

More cameras → more input sources → richer situational awareness → better
decisions. One agent already speaks *"look at one camera, several, or `all`"*,
keeps a per-camera event memory, and runs standing watches across them — so the
"sensor-fusion brain" pattern is **already the shape of the product**, not a
rebuild. A patrol robot can watch its own four cameras *and* tap the building's
fixed cameras as it moves; a drone can fuse its gimbal feed with a ground unit's
view. The decision layer sees the union.

## What already exists and is reusable (shipped today)

| Building block | Why it matters for autonomy |
|---|---|
| **Onboard-camera discovery** (`device:` + `auto_discover_cameras`) | The agent runs *on* the camera-bearing device with zero provisioning — the literal robot/drone case. |
| **Multi-camera tools + `all` + per-camera event rings** | The fusion substrate: reason across every input source at once. |
| **`rtsp://` / `http://` frame sources** | Pull in any remote stream the platform can reach. |
| **AI Adapter Contract + KAI-C** | Swap/add perception models (depth, thermal, open-vocab) without touching the core; every inference is audit-chained. |
| **Tiny Apache-2.0 models** (Qwen3 0.6–1.7B, YOLOv8n, faster-whisper, Piper) | Fit Jetson/Coral/Pi-class edge compute; permissively licensed for commercial/defense redistribution. |
| **Editions / nano tier + lazy footprint** | Right-size to the compute the platform actually carries. |
| **`local_only` sovereignty + offline default** | No vendor egress in denied/contested environments. |
| **Tool-calling decision pattern** | "If you see X, do Y" maps cleanly onto *high-level* autonomous decisioning. |

## Use cases — positioned by platform

The framework is one perception/decision brain; the use cases multiply with the
sensors and the platform. A non-exhaustive catalog:

| Platform | Use case | The OpenNVR job |
|---|---|---|
| **Ground robot (UGV)** | Security/patrol of a site or warehouse | Fuse onboard + fixed cameras; detect intrusions/anomalies; alarm + log, fully on-board |
| | Inspection (factories, substations, pipelines) | Open-vocab "find the corroded valve / open panel / leak"; flag + record with audit trail |
| | Warehouse/logistics | Count, track, verify pick/pack; line-crossing at zones |
| **Drone (UAV)** | Infrastructure survey (towers, bridges, solar, wind) | Detect defects across the gimbal feed; geo-tag findings; no cloud upload of sensitive imagery |
| | Agriculture | Crop/weed/pest spotting, stand counts, irrigation checks |
| | Search & rescue | Person/vehicle detection in disaster/wilderness, on-board, comms-denied |
| | Perimeter / wide-area watch | Fuse drone view with ground cameras for a single situational picture |
| **Underwater / marine (ROV/AUV/USV)** | Hull, pipeline, cable, aquaculture inspection | Damage/marine-growth detection on-vehicle where there's *no* connectivity at all |
| **Any platform** | Defense / regulated | The sovereign, NDAA-clean, auditable perception layer the cloud vendors can't legally provide |

The common thread: **sees locally, decides locally, keeps the evidence, sends
nothing to a vendor.** That's the position — across every one of these.

## What would need building (honest gaps)

OpenNVR is a perception + decision layer today, not a control stack. To serve
autonomy it needs:

1. **Closed-loop latency.** NVR tolerates ~seconds; navigation/avoidance needs
   10–100 ms. That means a **streaming inference path** (not poll-a-frame) and
   **hardware acceleration** (Jetson/Coral/GPU), with a clear budget per stage.
2. **An actuation bridge — keep OpenNVR the brain, don't reinvent control.** Add
   a relay/adapter to **ROS 2** (robots) and **MAVLink / PX4 / ArduPilot**
   (drones), so decisions become commands through the platform's real
   controller. OpenNVR advises/decides high-level; low-level control stays where
   it belongs.
3. **Multi-source sync & fusion at scale** — time alignment across feeds, source
   health/failover, and prioritization when bandwidth/compute is constrained.
4. **Offline-first resilience** under intermittent comms; deferred sync/audit.
5. **A safety envelope.** The AI layer makes *high-level* perception/decisions;
   the dedicated motion/flight controller owns the safety-critical loop and the
   hard limits. This is both the responsible posture and what serious
   industrial/defense buyers require.

## Architectural seams (where this plugs in)

- **New frame sources** beyond `device:`/`rtsp:` — a streaming/low-latency
  source and sensor adapters (thermal/depth) via the existing contract.
- **A "control" adapter** (the ROS2/MAVLink bridge) — the one genuinely new
  surface; everything upstream of it already exists.
- **An accelerated detector path** (GPU/Jetson build) — already on the #82
  roadmap for latency.
- **The agent's tool set** extended with movement/inspection *intents* (not raw
  control), which the control adapter translates and the safety layer bounds.

## Scope & status

Forward-looking direction, not a committed roadmap item. We position OpenNVR as
the **sovereign perception-and-decision brain for autonomous edge platforms** —
reusing ~80% of what already ships — explicitly *on top of* a real-time control
stack, with safety-critical control left to the dedicated controller and use
cases kept to inspection, navigation, monitoring, survey, and SAR.
