# Security Policy

OpenNVR is a security product. We take vulnerability reports seriously and
respond to them on a defined timeline. This document covers what we support,
how to report, what to expect, and what you should do to keep your deployment
secure.

## Supported versions

| Version | Status                       |
| ------- | ---------------------------- |
| 0.1.x   | :white_check_mark: supported |

Earlier development snapshots are not supported. Upgrade to the latest 0.1.x
release before reporting a vulnerability against an older build — if the issue
still reproduces on the latest tag, the report is in scope.

## Reporting a vulnerability

**Please do not open a public GitHub issue for sensitive security
vulnerabilities.** Public issues are immediately discoverable; we want a
chance to ship a fix before the world sees the report.

Preferred channel: [GitHub's private vulnerability reporting](https://github.com/open-nvr/open-nvr/security/advisories/new)
on this repository. It gives us a private thread, an audit trail, and a way to
credit you in the eventual advisory.

If you cannot use GitHub's reporting flow, email **security@cryptovoip.in**
with the subject line `OpenNVR security report` and as much detail as you can
share without exposing your own systems.

A good report includes:

- The OpenNVR version (`git describe` output or release tag)
- The deployment shape (Tier 0 compose, host-mode Linux, bare-metal dev)
- Reproducer — minimum steps that demonstrate the issue
- Impact — what an attacker can do once the bug is triggered
- Optional: a suggested fix or mitigation if you have one

## What to expect after you report

- **Within 48 hours:** acknowledgement that the report has been received and
  is being triaged.
- **Within 7 days:** initial assessment — confirmed or unable-to-reproduce —
  and a rough timeline for the fix.
- **At fix time:** a coordinated disclosure window (default 30 days, extended
  for severe issues that need an ecosystem fix). Reporters who want to be
  credited in the advisory are credited.
- **After release:** the advisory goes public on the [GitHub Security
  Advisories](https://github.com/open-nvr/open-nvr/security/advisories) tab
  with CVE assignment where applicable.

## Security architecture overview

OpenNVR is designed so the operator does not configure security — they
configure exceptions. Every security feature ships **on by default**:

- **No shipped default credentials.** First boot prints a one-time setup
  token; the operator chooses an admin password from that point on.
- **Strong-secret validator.** The core refuses to boot if `SECRET_KEY`,
  `INTERNAL_API_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, or `MEDIAMTX_SECRET`
  are placeholders or shorter than the minimum length.
- **Loopback-only by default.** MediaMTX binds to 127.0.0.1; outbound
  exposure is an explicit operator decision.
- **RTSPS / HLS-TLS / WebRTC-TLS on by default.** Plaintext RTSP requires
  opt-in plus an audit-log entry.
- **Offline-first network posture.** Cloud routes return 403 unless
  `DEPLOYMENT_MODE` is explicitly switched from `offline` to `hybrid`
  or `cloud` (and that switch is audit-logged at boot).
- **AI sovereignty enforcement.** Adapters declaring `network_egress` are
  refused under the default `local_only` policy.
- **End-to-end audit trail.** Every inference carries an `X-Correlation-Id`
  joining alert → middleware → adapter; events land in an append-only log.

Full threat model and control mapping in
[`docs/SECURITY_ARCHITECTURE.md`](docs/SECURITY_ARCHITECTURE.md). Academic
foundation at [Zenodo DOI 10.5281/zenodo.17261761](https://doi.org/10.5281/zenodo.17261761).

## Operator checklist

These are the obvious things you should still do for any internet-facing
deployment. None of them are OpenNVR-specific, but they're listed here so
nobody is surprised by them.

- **Generate strong secrets.** Run `./scripts/generate-secrets.sh --write`
  (Linux/macOS) or `.\scripts\generate-secrets.ps1 -Write` (Windows) before
  the first `docker compose up`. The validator will refuse to boot otherwise.
- **Restrict the `.env` file.** `chmod 600 .env` so other users on the host
  can't read it.
- **Front the service with TLS.** OpenNVR speaks plain HTTP on port 8000;
  put a reverse proxy with a real certificate in front of it before exposing
  it to anything outside your LAN.
- **Firewall the streaming ports.** MediaMTX's RTSPS (8322), HLS (8888), and
  WebRTC (8889) should be reachable only from clients that need them.
- **Back up the database.** The `opennvr_db_data` volume holds your camera
  list, user accounts, and audit log. Take periodic snapshots.

## Out of scope

- **Anything in the dev shell.** `./start.sh build` and the bare-metal
  developer setup in `docs/LOCAL_SETUP.md` are intended for contributors
  on trusted machines.
- **Third-party adapter container images.** OpenNVR validates that
  contract-compliant adapters can register, but it does not vouch for the
  contents of any adapter image that didn't come from the official `open-nvr`
  GitHub organisation. Run untrusted adapters at your own risk.
- **The model behaviour itself.** Hallucinated detections, biased
  recognition results, and other ML-quality issues are bugs in the model,
  not in OpenNVR's transport / audit layer. File them upstream with the
  model author.
