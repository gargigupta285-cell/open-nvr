#!/usr/bin/env bash
# ============================================================
# Regression test for start.sh's compose-file auto-selection
# (ISSUE-12 + ISSUE-13).
#
# CONTEXT
# -------
# start.sh detects the OS and picks a default compose file. The
# historical Linux default (docker-compose.linux.yml) was a strict
# functional subset of docker-compose.tier0.yml — no nginx TLS
# edge, no yolov8-weights-init, no yolov8-adapter, no nats — yet
# start.sh's print_access_urls always printed `https://<lan-ip>/`
# as if nginx were present. Operators following the printed URL
# on a Linux deploy hit "connection refused" on :443 because
# nothing was listening there.
#
# The fix: switch the Linux default to docker-compose.tier0.yml
# (the canonical hardened path) so the printed URL is always
# accurate and the README's "detection out of the box" promise
# is actually delivered. linux.yml stays available via an opt-in
# OPENNVR_COMPOSE_FILE env var for operators who specifically
# need the host-networking variant (rare — usually for ONVIF
# multicast camera discovery on a single-LAN topology).
#
# WHAT THIS TEST DOES
# -------------------
# (1) Asserts start.sh's Linux case-arm points at tier0.yml.
# (2) Asserts the OPENNVR_COMPOSE_FILE env-var override hook
#     exists (so testing tools can short-circuit detection).
# (3) Asserts every service that print_access_urls advertises
#     (nginx for TLS, yolov8-* for detection, nats for events)
#     is present in whatever start.sh picks by default on Linux.
# ============================================================

set -u

. "$(dirname "$0")/_lib.sh"
require_python_yaml

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TESTS_RUN=0
TESTS_FAILED=0
start_test() { TESTS_RUN=$((TESTS_RUN + 1)); printf "  [%2d] %s ... " "$TESTS_RUN" "$1"; }
pass() { echo "PASS"; }
fail() { echo "FAIL"; echo "      $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); }

echo "Running compose-file-selection tests"
echo ""

# ── 1. start.sh's Linux default is tier0.yml (not linux.yml) ──
start_test "start.sh Linux case-arm selects docker-compose.tier0.yml"
# Parse the case statement; look for the Linux*) arm and check
# what COMPOSE_FILE it assigns. Awk picks the block between
# ``Linux*)`` and the first ``;;``.
linux_arm=$(awk '/Linux\*\)/,/;;/' "${REPO_ROOT}/start.sh")
linux_default=$(echo "$linux_arm" | grep -oE 'COMPOSE_FILE="[^"]+"' | head -1 | sed 's/.*="//; s/"$//')
if [ "$linux_default" = "docker-compose.tier0.yml" ]; then
    pass
else
    fail "start.sh's Linux default is '${linux_default}' (expected docker-compose.tier0.yml).
The historical linux.yml was a strict subset of tier0.yml (no nginx,
no detection, no events bus) yet start.sh's printed URL assumed
nginx was present. Reverting to linux.yml regresses ISSUE-12+13."
fi

# ── 2. OPENNVR_COMPOSE_FILE override hook exists ──
# So operators who specifically need host networking can opt back
# into linux.yml: OPENNVR_COMPOSE_FILE=docker-compose.linux.yml ./start.sh up
start_test "OPENNVR_COMPOSE_FILE env-var override hook is present"
if grep -q 'OPENNVR_COMPOSE_FILE' "${REPO_ROOT}/start.sh"; then
    pass
else
    fail "start.sh must honor an OPENNVR_COMPOSE_FILE env var so operators
can opt into the host-networking variant (linux.yml) when needed."
fi

# ── 3. Default compose file has the services start.sh advertises ──
# print_access_urls always prints ``https://<lan-ip>/`` — that ONLY
# works if nginx is present (it's the TLS edge). If the default
# compose file lacks nginx, the printed URL is a lie.
start_test "default compose file (Linux) ships nginx for the TLS edge"
if python3 - "${REPO_ROOT}" "${linux_default}" <<'PY'
import sys, yaml
from pathlib import Path
repo, fn = sys.argv[1], sys.argv[2]
c = yaml.safe_load((Path(repo) / fn).read_text())
services = (c.get("services") or {})
sys.exit(0 if "nginx" in services and "nginx-certs-init" in services else 1)
PY
then
    pass
else
    fail "${linux_default} is missing nginx and/or nginx-certs-init.
start.sh's print_access_urls prints https://<lan-ip>/ — that requires
nginx to be present as the TLS edge."
fi

# ── 4. Default compose ships YOLOv8 detection out-of-the-box ──
# The README promises "YOLOv8 detection out of the box". The
# default compose file must include yolov8-weights-init and
# yolov8-adapter (or an equivalent) — not gate them behind an
# opt-in profile that the README never mentions.
start_test "default compose file (Linux) ships YOLOv8 detection out-of-the-box"
if python3 - "${REPO_ROOT}" "${linux_default}" <<'PY'
import sys, yaml
from pathlib import Path
repo, fn = sys.argv[1], sys.argv[2]
c = yaml.safe_load((Path(repo) / fn).read_text())
services = (c.get("services") or {})
has_weights = "yolov8-weights-init" in services
has_adapter = "yolov8-adapter" in services
# Adapter must NOT be hidden behind a profile (operators don't
# discover --profile flags from the quickstart).
adapter_profiles = services.get("yolov8-adapter", {}).get("profiles") or []
ok = has_weights and has_adapter and not adapter_profiles
sys.exit(0 if ok else 1)
PY
then
    pass
else
    fail "${linux_default} must ship yolov8-weights-init + yolov8-adapter
(not profile-gated) so the README's 'detection out of the box'
promise is actually delivered on the default install path."
fi

# ── 5. Default compose ships nats for the events bus ──
start_test "default compose file (Linux) ships nats for the events bus"
if python3 - "${REPO_ROOT}" "${linux_default}" <<'PY'
import sys, yaml
from pathlib import Path
repo, fn = sys.argv[1], sys.argv[2]
c = yaml.safe_load((Path(repo) / fn).read_text())
sys.exit(0 if "nats" in (c.get("services") or {}) else 1)
PY
then
    pass
else
    fail "${linux_default} must ship nats — downstream services and the
audit log expect to subscribe to opennvr.inference.* / opennvr.alerts.*
subjects. linux.yml omitted nats, which silently broke that pipeline."
fi

# ── 6. start.sh's printed scheme matches what the compose serves ──
# print_access_urls hard-codes https://. The default compose must
# therefore have a TLS terminator listening on :443 (nginx). Tested
# in test 3 indirectly; this test is the explicit scheme contract.
start_test "print_access_urls scheme (https://) matches default-compose listener"
# Two things must align: print_access_urls says https://, AND the
# default compose has nginx publishing port 443.
if grep -qE 'Web UI.*https://' "${REPO_ROOT}/start.sh" && \
   python3 - "${REPO_ROOT}" "${linux_default}" <<'PY'
import sys, yaml
from pathlib import Path
repo, fn = sys.argv[1], sys.argv[2]
c = yaml.safe_load((Path(repo) / fn).read_text())
nginx = (c.get("services") or {}).get("nginx", {})
ports = nginx.get("ports") or []
has_443 = any("443" in str(p) for p in ports)
sys.exit(0 if has_443 else 1)
PY
then
    pass
else
    fail "Scheme drift: start.sh prints https://<lan-ip>/ but the default
Linux compose either lacks nginx or doesn't publish :443. If you
change one, change the other — operators following the URL hit
'connection refused' otherwise."
fi

# ── Summary ────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then echo "Result       : all green"; exit 0
else echo "Result       : failures"; exit 1; fi
