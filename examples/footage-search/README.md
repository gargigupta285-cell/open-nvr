# footage-search

**Search your recorded footage in plain language — "show me every red
truck at the dock yesterday."** A natural-language index over the
inference history OpenNVR already produces, fully on your hardware, no
cloud and no API keys.

```
$ python footage_search.py search --config config.yml "red truck at the dock yesterday"
2 match(es):
  [cam-dock] 2026-06-13 14:22:08  person truck
      "a red truck parked near a loading dock"
      correlation_id=corr-A (use it to pull the recorded segment)
```

Two query paths: the CLI above, and the **App Catalog's "Search
footage" form** — the manifest declares a `search` action, so the
catalog renders the form and proxies it to this app's contract surface
(user-JWT only) with zero frontend code in this repo folder.

| | |
|---|---|
| Pattern | Indexer subscribes to NATS inference events → SQLite; catalog-action + CLI search |
| Adapters | (rides upstream's detector + a BLIP-style captioner — no direct call) |
| Difficulty | ⭐⭐⭐ advanced |
| Best for learning | Building a searchable index off the event bus, NL→filter parsing |

## Why this works without a special model

Object **classes** ("truck") come from your detector's labels. Object
**attributes** ("red") come from a captioner's scene text (the `blip`
adapter emits captions like *"a red truck near a loading dock"*). The
indexer **merges** a detection event and a caption event that share a
`correlation_id` into one searchable keyframe, so a query can match a
label AND a caption word on the same frame. That's the whole trick — and
it runs on adapters you already have.

> **Want precise attributes?** Point the indexer at an open-vocabulary /
> VLM adapter (see the `vlm` adapter in the
> [ai-adapter](https://github.com/open-nvr/ai-adapter) repo) instead of —
> or alongside — BLIP. The index and search code don't change; you just
> get sharper "red", "wearing a backpack", "license plate ABC-123"
> matching.

## Run it

Two subcommands. Run the indexer as a daemon alongside your detectors,
then search whenever you like:

```bash
cd examples/footage-search && uv sync --extra dev
cp config.example.yml config.yml      # edit db_path, nats_url, camera aliases

# 1. Build the index (runs until Ctrl-C; pays zero adapter cost)
python footage_search.py index --config config.yml

# 2. Search it
python footage_search.py search --config config.yml "people near the gate in the last hour"
python footage_search.py search --config config.yml "anyone in a yellow jacket today"
python footage_search.py search --config config.yml "suitcase left in the lobby"
```

Each result carries the `correlation_id` that ties it to the exact
recorded segment in OpenNVR, so you can jump straight to the clip.

## How queries are parsed

By default a **heuristic parser** (no LLM, deterministic) extracts:

- **labels** — object classes named in the query (COCO vocabulary plus
  your `extra_labels`);
- **keywords** — leftover descriptive words (colors, clothing) matched
  against captions;
- **time window** — `yesterday`, `today`, `this morning`, `tonight`,
  `last 30 minutes`, `past hour`, …;
- **camera** — via `camera_aliases` ("the dock" → `cam-dock`).

Set `ollama.enabled: true` to parse with a local Ollama model instead —
better at messy phrasing, and it **falls back to the heuristic parser on
any error**, so turning it on never makes search worse.

## Configure

Everything is in [`config.example.yml`](config.example.yml): the SQLite
`db_path`, the `nats_url`, `extra_labels`, `camera_aliases`, and the
optional `ollama` block.

## What it does NOT do (yet)

- **No bounding-box/colour grounding.** "Red" matches caption text, not a
  verified red region — a captioner can miss or misname colours. The VLM
  adapter tightens this.
- **No frame thumbnails.** Results are text rows + `correlation_id`; pair
  with OpenNVR's playback API to render clips. (Follow-up.)
- **No semantic embeddings.** Matching is keyword/label based, not vector
  similarity, so paraphrases ("lorry" for "truck") won't match unless you
  add the synonym. An embedding backend behind the `FootageStore`
  interface is the natural upgrade.

## Tests

```bash
uv run pytest          # or: PYTHONPATH=. python -m pytest tests/ -q
```

Covers the NL→filter parser (labels, keywords, time windows), keyframe
extraction + the correlation-id merge, the headline "red truck"
end-to-end path, and time-window filtering — all without NATS or an LLM.
