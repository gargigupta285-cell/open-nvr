# abandoned-object (unattended item)

**Alert when a bag, suitcase, or box is left stationary and unattended
in a zone.** The classic "unattended baggage" primitive for transport
hubs, lobbies, and secure perimeters.

A NATS-subscribing monitoring app. It rides the detection stream that's
already running (zero adapter/GPU cost), so it needs a **tracked** stream
— chain the `bytetrack` adapter so a stationary object keeps a stable
`track_id`.

| | |
|---|---|
| Pattern | Subscribes to NATS inference events (tracked) → fires alerts |
| Adapter | (rides upstream's detector + `bytetrack` — no direct call) |
| Difficulty | ⭐⭐⭐ advanced |
| Best for learning | Multi-track state, spatial proximity suppression, anchor/dwell logic |

## What it does

For each watched-object track inside a zone it remembers an **anchor**
(where the object settled) and when. While the object stays within
`move_tolerance_px` of that anchor it's *stationary*; drift further and
the anchor resets (it was carried, not left). When a track has been
stationary for `dwell_seconds` **and** no `person` has been within
`person_radius_px` of it during the last `owner_grace_seconds`, it fires
once.

The person-proximity suppression is the important part: a bag next to its
owner is not "abandoned." Only once the likely owner has left its
vicinity does the alert fire.

## Run it

```bash
cd examples/abandoned-object && uv sync --extra dev
cp config.example.yml config.yml      # edit zone, object labels, thresholds
python abandoned_object.py --config config.yml
```

> **Wire up tracking first.** Without `track_id` on detections this app
> can't tell that the *same* object has been sitting still — it logs a
> one-time warning and ignores untracked objects. Chain `bytetrack`.

## Configure

See [`config.example.yml`](config.example.yml). The key knobs:

- **`object_labels`** / **`person_label`** — what can be abandoned, and
  what counts as an owner.
- **`dwell_seconds`** — how long unattended before it fires.
- **`move_tolerance_px`** — how still "stationary" means.
- **`person_radius_px`** / **`owner_grace_seconds`** — the
  owner-proximity suppression window.

## How alerts flow

Same §11.5 wire shape as every example — stdout always, plus optional
webhook and NATS publish to
`opennvr.alerts.app.abandoned-object.{camera_id}`, consumed by
[`alerts-subscriber`](../alerts-subscriber) and
[`home-assistant-relay`](../home-assistant-relay) unchanged.

## What it does NOT do (yet)

- **No owner re-identification.** Suppression is purely spatial (a person
  *near* the object), not "the same person who carried it in." A passer-by
  standing close briefly resets the grace window.
- **No COCO-beyond labels.** Limited to what your detector emits. "Box"
  or "package" needs a detector trained for them (or the open-vocab/VLM
  adapter).
- **No left-vs-removed distinction.** It fires on abandonment, not on an
  object disappearing from a fixed display — that's a different predicate.

## Tests

```bash
uv run pytest          # or: PYTHONPATH=. python -m pytest tests/ -q
```

Covers the dwell trigger, owner-proximity suppression, the owner-leaves
path, anchor reset on movement, fire-once latching, and untracked-object
handling.
