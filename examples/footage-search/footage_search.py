# Copyright (c) 2026 OpenNVR
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Footage-search example app — natural-language search over recorded
inference history.

    $ python footage_search.py search "red truck at the dock yesterday"

    2 matches:
      [cam-dock] 2026-06-13 14:22:08  truck
        "a red truck parked near a loading dock"
        correlation_id=corr_8f1c… (use it to pull the recorded segment)
      [cam-dock] 2026-06-13 09:05:41  truck person
        "a red delivery truck with a person beside it"
        correlation_id=corr_2a90…

Two subcommands:

* ``index`` runs a daemon that subscribes to KAI-C's NATS inference
  broadcast surface and writes every searchable keyframe (object labels
  from a detector, scene captions from a BLIP-style captioner) into a
  local SQLite index. Run it alongside your detectors; it pays zero
  adapter cost (it rides the stream that's already flowing).

* ``search`` parses a natural-language query into a structured filter
  (object labels + descriptor keywords + time window + camera) and runs
  it against the index, printing matching keyframes newest-first. Each
  match carries the ``correlation_id`` that ties it to the exact
  recorded segment in OpenNVR.

Why this works without a special model: object *classes* come from the
detector's labels; *attributes* like "red" come from the captioner's
scene text. Searching across both is what lets "red truck" resolve. For
precise attribute detection, point the indexer at an open-vocabulary /
VLM adapter instead of (or alongside) BLIP — the index and search code
don't change.

Run::

    python footage_search.py index  --config config.yml
    python footage_search.py search --config config.yml "red truck yesterday"
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from query import DEFAULT_LABELS, parse_heuristic, parse_with_ollama
from store import FootageStore, SearchResult, keyframe_from_event

logger = logging.getLogger("footage-search")


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class OllamaConfig:
    enabled: bool = False
    url: str = "http://ollama:11434"
    model: str = "llama3.2"


@dataclass
class AppConfig:
    db_path: str
    nats_url: str
    nats_token: str | None
    subject_pattern: str
    extra_labels: list[str]
    camera_aliases: dict[str, str]
    ollama: OllamaConfig
    result_limit: int


def load_config(path: str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"config {path!r}: root must be a mapping")

    db_path = str(raw.get("db_path") or "footage_index.sqlite3").strip()
    if not db_path:
        raise ValueError("config: 'db_path' must not be empty")

    nats_url = str(raw.get("nats_url") or "").strip()
    if not nats_url:
        raise ValueError("config: 'nats_url' is required (for `index`)")
    subject = str(raw.get("subject_pattern") or "opennvr.inference.>").strip()

    extra_labels = [str(s).lower() for s in (raw.get("extra_labels") or [])]

    aliases_raw = raw.get("camera_aliases") or {}
    if not isinstance(aliases_raw, dict):
        raise ValueError("config: 'camera_aliases' must be a mapping of word -> camera_id")
    camera_aliases = {str(k).lower(): str(v) for k, v in aliases_raw.items()}

    ollama_raw = raw.get("ollama") or {}
    ollama = OllamaConfig(
        enabled=bool(ollama_raw.get("enabled", False)),
        url=str(ollama_raw.get("url", "http://ollama:11434")),
        model=str(ollama_raw.get("model", "llama3.2")),
    )

    try:
        result_limit = int(raw.get("result_limit", 25))
    except (TypeError, ValueError) as exc:
        raise ValueError("config: 'result_limit' must be an integer") from exc
    if result_limit <= 0:
        raise ValueError("config: 'result_limit' must be > 0")

    return AppConfig(
        db_path=db_path,
        nats_url=nats_url,
        nats_token=str(raw["nats_token"]) if raw.get("nats_token") else None,
        subject_pattern=subject,
        extra_labels=extra_labels,
        camera_aliases=camera_aliases,
        ollama=ollama,
        result_limit=result_limit,
    )


# ── Indexer ────────────────────────────────────────────────────────


class Indexer:
    """Subscribes to NATS inference events and writes searchable
    keyframes into the store."""

    def __init__(self, config: AppConfig, store: FootageStore) -> None:
        self._config = config
        self._store = store
        self._stop_event = asyncio.Event()
        self._nc: Any = None
        self._indexed = 0

    def stop(self) -> None:
        self._stop_event.set()

    def ingest(self, event: dict[str, Any]) -> bool:
        """Index one event. Returns True if a keyframe was stored."""
        kf = keyframe_from_event(event)
        if kf is None:
            return False
        self._store.add(kf)
        self._indexed += 1
        return True

    async def run(self, *, once: bool = False) -> None:
        import nats

        connect_kwargs: dict[str, Any] = {
            "servers": [self._config.nats_url],
            "connect_timeout": 5.0,
            "reconnect_time_wait": 1.0,
            "max_reconnect_attempts": -1,
        }
        if self._config.nats_token:
            connect_kwargs["token"] = self._config.nats_token
        self._nc = await nats.connect(**connect_kwargs)
        logger.info(
            "footage-search indexer started: db=%s, subject=%r (%d rows already indexed)",
            self._config.db_path, self._config.subject_pattern, self._store.count(),
        )
        try:
            sub = await self._nc.subscribe(self._config.subject_pattern)
            async for msg in sub.messages:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                try:
                    self.ingest(payload)
                except Exception:
                    logger.exception("ingest failed for subject=%s", msg.subject)
                if self._indexed and self._indexed % 100 == 0:
                    logger.info("indexed %d keyframes", self._indexed)
                if once:
                    self.stop()
                if self._stop_event.is_set():
                    break
        finally:
            try:
                await self._nc.drain()
            except Exception:
                try:
                    await self._nc.close()
                except Exception:
                    pass
            logger.info("indexer stopped; %d keyframes this session", self._indexed)


# ── Search ─────────────────────────────────────────────────────────


def run_search(config: AppConfig, store: FootageStore, query: str) -> list[SearchResult]:
    """Parse the query and run it against the store."""
    now = _dt.datetime.now(_dt.timezone.utc)
    vocab = set(DEFAULT_LABELS) | set(config.extra_labels)
    if config.ollama.enabled:
        qf = parse_with_ollama(
            query, now=now, ollama_url=config.ollama.url, model=config.ollama.model,
            label_vocab=vocab, camera_aliases=config.camera_aliases,
        )
    else:
        qf = parse_heuristic(
            query, now=now, label_vocab=vocab, camera_aliases=config.camera_aliases,
        )
    logger.debug(
        "parsed query → labels=%s keywords=%s camera=%s since=%s until=%s",
        qf.labels, qf.keywords, qf.camera_id, qf.since, qf.until,
    )
    return store.search(
        labels=qf.labels, keywords=qf.keywords,
        since=qf.since, until=qf.until, camera_id=qf.camera_id,
        limit=config.result_limit,
    )


def format_results(results: list[SearchResult]) -> str:
    if not results:
        return "No matching footage found."
    lines = [f"{len(results)} match(es):"]
    for r in results:
        when = _dt.datetime.fromtimestamp(r.ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        labels = " ".join(r.labels) or "—"
        lines.append(f"  [{r.camera_id}] {when}  {labels}")
        if r.caption:
            lines.append(f"      \"{r.caption}\"")
        cid = r.correlation_id or "—"
        lines.append(
            f"      correlation_id={cid} (use it to pull the recorded segment)"
        )
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="footage-search",
        description="Index inference events and search recorded footage in natural language.",
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Run the indexer daemon (subscribes to NATS).")
    p_search = sub.add_parser("search", help="Search the index.")
    p_search.add_argument("query", help="Natural-language query, e.g. 'red truck yesterday'.")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    store = FootageStore(config.db_path)
    try:
        if args.command == "search":
            results = run_search(config, store, args.query)
            print(format_results(results))
            return 0

        # index
        indexer = Indexer(config, store)
        loop = asyncio.new_event_loop()

        def _handle_signal(_signum, _frame):
            logger.info("signal received, stopping…")
            loop.call_soon_threadsafe(indexer.stop)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        try:
            loop.run_until_complete(indexer.run())
        finally:
            loop.close()
        return 0
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
