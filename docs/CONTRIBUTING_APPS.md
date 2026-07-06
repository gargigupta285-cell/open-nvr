# Submitting an app to the OpenNVR App Store

This is the developer-facing guide for getting **your** app into the curated
OpenNVR App Store — the browse-and-install catalog an operator sees under
**Settings → App Catalog**. The model is the same one Homebrew taps and the
Home Assistant add-on store use: **you build and publish your app; you open a
PR that adds one entry to a curated index; a reviewer merges it.**

The index is one file — [`server/config/apps_index.yml`](../server/config/apps_index.yml).
Landing your app there is five steps:

1. [Build your app on the App SDK](#1-build-your-app-on-the-app-sdk)
2. [Publish your image and get its digest](#2-publish-your-image-and-get-its-digest)
3. [Add one entry to `apps_index.yml`](#3-add-one-entry-to-apps_indexyml)
4. [Validate it — `make validate-apps-index`](#4-validate-it)
5. [Open the PR](#5-open-the-pr)

---

## The trust model (read this first)

The App Store index is **curated and reviewed**, not open-write. Anyone can
propose an app; a maintainer reviews and merges the PR. That review is the
whole security boundary, because the index is what the one-click installer
trusts:

- The install endpoints (`POST /apps/index/{id}/install`) copy `image` and
  `image_digest` **from the curated index entry, never from the caller** —
  an operator can only install an app that a reviewer already vetted into
  this file (see [`docs/APPS_INSTALL.md`](APPS_INSTALL.md)).
- **A pinned `image_digest` is what makes one-click install trustworthy.**
  With a digest, the reconciler deploys `image@sha256:…` — the exact bytes
  the review vouched for (supply-chain integrity). Without one, it logs a
  loud `UNPINNED — dev only` warning and installs the floating tag. **A
  production / sovereign deployment installs only apps whose entry carries a
  digest**, so pin yours.

So your entry earns trust by being reviewable: the image is pinned, the
declared tasks are real, the manifest matches, and there are no secrets in
the file. The rest of this guide is how to satisfy that.

---

## 1. Build your app on the App SDK

Your app is a container that speaks the OpenNVR App SDK contract. It
subscribes to the stack's NATS inference stream (or drives KAI-C directly),
serves the SDK's HTTP endpoints (`/health` `/manifest` `/state`), and
**self-registers with the app registry on boot** using the deployment's
`INTERNAL_API_KEY`. Once registered it shows up in the App Catalog with a
live status dot and an auto-generated config form.

> **New to the SDK? Start with the on-ramp.**
> [`docs/FIRST_DETECTOR.md`](FIRST_DETECTOR.md) — "Your first OpenNVR detector
> in 15 minutes" — scaffolds a runnable app with `scripts/create_opennvr_app.py`,
> walks you through filling in the rule and getting its tests green, and lands
> you right back here at step 3 to publish. Come back once you have a working
> app under `examples/<id>/`.

- **SDK + manifest.** Build on
  [`sdk/opennvr-app-sdk/`](../sdk/opennvr-app-sdk/). Every app ships an
  [`AppManifest`](../sdk/opennvr-app-sdk/opennvr_app_sdk/manifest.py) — its
  declarative identity: `id`, `name`, `version`, `category`, `summary`,
  `requires_tasks`, `params`, `emits`. **The index entry mirrors this
  manifest** (see step 3), so decide these here first.
- **Start from a shipped example.** Everything under
  [`examples/`](../examples/README.md) is a copy-as-template starting point.
  The examples README's grid (drives-inference vs subscribes;
  inference-events vs alerts) tells you which shape is closest to yours —
  copy that folder and replace the predicate. That grid *is* the "two doors"
  mental model of the platform: an app either drives inference or rides the
  event bus.
- **Declare real tasks.** `requires_tasks` names the adapter task types your
  app depends on (`object_detection`, `multi_object_tracking`,
  `face_recognition`, `ocr`, `image_captioning`, …). These are the
  free-text task names from the adapter contract —
  [`docs/AI_ADAPTER_CONTRACT.md` §4 (`tasks_advertised`)](AI_ADAPTER_CONTRACT.md).
  The vocabulary is deliberately open, but **prefer a canonical name** from
  [`server/config/use_case_map.yml`](../server/config/use_case_map.yml) when
  one fits — the catalog greys out an app whose tasks no installed adapter
  advertises, so a task nobody provides makes your app un-runnable.

## 2. Publish your image and get its digest

Publish to GHCR (recommended) or any registry the operator's host can pull
from. Then capture the **immutable digest** your tag resolved to — that is
what pins the install.

```bash
# Build + push (GitHub Container Registry example)
docker build -t ghcr.io/<you>/my-app:1.0.0 -t ghcr.io/<you>/my-app:latest examples/my-app
docker push ghcr.io/<you>/my-app:1.0.0
docker push ghcr.io/<you>/my-app:latest

# Read back the sha256 digest the tag now points at
docker buildx imagetools inspect ghcr.io/<you>/my-app:latest --format '{{.Manifest.Digest}}'
# -> sha256:3f8c...e91   (this is your image_digest)
```

Make the package **public** so an operator can pull it without a login.

> Don't have a published image yet? An app that ships only as a local
> `build:` overlay (like the first-party detectors today) may omit
> `image_digest` and set `build_context` instead — but it will install
> **unpinned (dev only)**. Publish + pin before you expect production
> operators to install it.

## 3. Add one entry to `apps_index.yml`

Append **one** entry to
[`server/config/apps_index.yml`](../server/config/apps_index.yml). Copy the
annotated template —
[`docs/apps-index-entry.template.yml`](apps-index-entry.template.yml) — and
fill every field. Here it is inline for reference:

```yaml
- id: my-app                       # unique, kebab-case; matches AppManifest.id + examples/<id>/
  name: My App                     # human title on the catalog card
  summary: Alerts when <the thing your app watches for> happens.
  category: perimeter              # perimeter | analytics | vehicle | doorstep | forensics | integration
  version: 1.0.0                   # matches AppManifest.version (semver)
  image: ghcr.io/<you>/my-app:latest       # well-formed ref: ghcr.io/... or opennvr/...
  image_digest: sha256:3f8c...e91          # from step 2 — pins the exact bytes (supply-chain integrity)
  requires_tasks: [object_detection]       # mirrors AppManifest.requires_tasks; prefer canonical names
  emits: [my_alert]                        # mirrors AppManifest.emits
  docs_url: examples/my-app/README.md      # your app's README
  install:
    compose: |                     # the exact copy-paste an operator runs; secrets via ${VAR} ONLY
      services:
        my-app:
          profiles: [apps]
          image: ghcr.io/<you>/my-app:latest
          restart: unless-stopped
          environment:
            - OPENNVR_INTERNAL_API_KEY=${INTERNAL_API_KEY}   # from .env — never a literal
            - NATS_URL=nats://nats:4222
            - OPENNVR_URL=http://opennvr-core:8000
          expose:
            - "9210"
          networks:
            - opennvr_internal
    command: docker compose -f docker-compose.yml -f docker-compose.apps.yml --profile apps up -d my-app
```

Rules the validator enforces (details in step 4):

- `id`, `name`, `summary`, `category`, `version`, `image`, `requires_tasks`,
  `docs_url`, and `install` (with a non-empty `compose` **and** `command`)
  are all required.
- `id` is unique and kebab-case.
- `image` is a well-formed `ghcr.io/...` or `opennvr/...` ref;
  `image_digest`, if present, is `sha256:` + 64 hex chars.
- **No secrets.** The compose block references `${VAR}` placeholders (e.g.
  `${INTERNAL_API_KEY}`) from the operator's `.env` — never a literal
  key/password/token.

## 4. Validate it

Run the submission gate before you push. It's stdlib + PyYAML only, so it
works in a clean checkout with no backend running:

```bash
make validate-apps-index
# or directly:
python3 scripts/validate_apps_index.py
```

It prints one clear message per problem and exits non-zero on any hard
failure — required-field gaps, a non-kebab or duplicate `id`, a malformed
`image` / `image_digest`, a plaintext secret, or an empty install block. An
**unknown `requires_tasks` name only warns** (free-text is allowed by the
adapter contract), nudging you toward a canonical task name. The same
validator runs in CI over the shipped index
(`server/tests/test_validate_apps_index.py`), so a green local run is the
signal your entry will pass review mechanically.

## 5. Open the PR

Branch off `main`, commit your one-entry addition, and open a PR (see the
general flow in [`CONTRIBUTING.md`](../CONTRIBUTING.md)). Keep it to the
index entry plus your app under `examples/<id>/` — one topic per PR.

**What reviewers check:**

- **Declared tasks are real / canonical.** `requires_tasks` names tasks an
  adapter can actually advertise — canonical where one fits
  (`use_case_map.yml`), and not a task nobody provides.
- **The image is pinned.** `image_digest` is present and correct, so
  one-click install is trustworthy. An unpinned entry is dev-only and won't
  be recommended for production operators.
- **The manifest matches.** `id` / `name` / `version` / `category` /
  `summary` / `requires_tasks` / `emits` in the index agree with your
  app's `AppManifest` — the index is a mirror, not a second source of truth.
- **No secrets.** The compose block uses `${VAR}` placeholders only; no
  literal key ever lands in the file.
- **The app self-registers.** On boot your app calls `POST /apps/register`
  with the deployment's `INTERNAL_API_KEY` and appears in the catalog — so
  "installed" actually means "running and registered".

Once merged, your app is browsable in every OpenNVR deployment's App Catalog
and installable via the copy-paste command (always) or one click (where the
operator has opted in). If yours is a first-party example, your name goes
on it.

---

## Related reading

- [`docs/FIRST_DETECTOR.md`](FIRST_DETECTOR.md) — the on-ramp: scaffold a
  runnable app with the generator, fill in the rule, get its tests green, run
  it against the stack — then land here to publish it.
- [`docs/APPS_INSTALL.md`](APPS_INSTALL.md) — the install security model:
  desired-state + reconciler, RBAC, digest pinning, audit. Explains *why*
  the pinned digest in your entry matters.
- [`docs/AI_ADAPTER_CONTRACT.md`](AI_ADAPTER_CONTRACT.md) §4 — the
  `tasks_advertised` vocabulary your `requires_tasks` reference.
- [`examples/README.md`](../examples/README.md) — the gallery, the
  drives-vs-subscribes grid, and how an example folder is structured.
- [`docs/apps-index-entry.template.yml`](apps-index-entry.template.yml) —
  the copy-paste entry template.
