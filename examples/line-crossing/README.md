# line-crossing (tripwire)

**Fire an alert when a tracked person or vehicle crosses a line in a
chosen direction.** Perimeter tripwire, directional entry/exit counter,
one-way corridor enforcement, loading-dock gate traffic.

A NATS-subscribing monitoring app. Unlike the zone-based examples, a
tripwire needs **per-object identity** — it has to know the *same* object
moved from one side of the line to the other. It therefore consumes
detections that carry a `track_id` (chain the `bytetrack` adapter after
your detector, or use a detector that tracks natively). It pays zero
adapter/GPU cost on top of the tracked stream that's already running.

| | |
|---|---|
| Pattern | Subscribes to NATS inference events (tracked) → fires alerts |
| Adapter | (rides upstream's detector + `bytetrack` — no direct call) |
| Difficulty | ⭐⭐ intermediate |
| Best for learning | Per-track state, directional segment-crossing geometry |

## What it does

Per `(camera, tripwire, track_id)` it remembers the track's previous
center point. When the next center arrives, it tests whether the segment
`previous → current` crosses the tripwire **and** flips to the other
side. If it does, and the direction matches the wire's `count_direction`,
it fires once for that crossing. Idle tracks are forgotten after
`track_ttl_seconds` so memory stays bounded.

**Direction convention:** the tripwire is an oriented segment A→B. An
object starting on the *left* of the A→B vector and ending on the right
is `a_to_b`; the reverse is `b_to_a`. Set `count_direction` to `a_to_b`,
`b_to_a`, or `both`. Unsure which way is which? Run with `both`, read the
`direction` field on the alerts, then pin it down.

## Run it

```bash
cd examples/line-crossing && uv sync --extra dev
cp config.example.yml config.yml      # edit camera URLs, line endpoints, direction
python line_crossing.py --config config.yml
```

> **Wire up tracking first.** Without `track_id` on detections this app
> can't define a crossing — it logs a one-time warning and ignores
> untracked detections. Chain the `bytetrack` adapter after your detector
> so events carry stable track IDs.

## Configure

See [`config.example.yml`](config.example.yml). The key knobs:

- **`line.a` / `line.b`** — the two endpoints of the tripwire, in pixel
  coordinates.
- **`line.count_direction`** — `a_to_b`, `b_to_a`, or `both`.
- **`watch_labels`** — which labels to track across the line.
- **`track_ttl_seconds`** — how long to remember an idle track.

## How alerts flow

Same §11.5 wire shape as every example — stdout always, plus optional
webhook and NATS publish to
`opennvr.alerts.app.line-crossing.{camera_id}`, consumed by
[`alerts-subscriber`](../alerts-subscriber) and
[`home-assistant-relay`](../home-assistant-relay) unchanged.

## What it does NOT do (yet)

- **No running totals.** It fires per crossing; it doesn't keep an
  in/out tally. A small [`alerts-subscriber`](../alerts-subscriber) that
  increments counters by `direction` closes that loop.
- **No multi-segment polylines.** One straight segment per wire. Model a
  jagged boundary as several cameras/wires, or extend `line.py`.
- **No re-identification across cameras.** Track IDs are per camera; a
  person leaving cam-A and entering cam-B is two tracks.

## Tests

```bash
uv run pytest          # or: PYTHONPATH=. python -m pytest tests/ -q
```

Covers the directional crossing geometry (including the `count_direction`
filter and the grazing-the-line edge case) and the per-track
`handle_event` state machine.
