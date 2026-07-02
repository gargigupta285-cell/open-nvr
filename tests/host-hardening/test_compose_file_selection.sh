#!/usr/bin/env bash
# Regression tests for the canonical Compose-file selection.
# Every supported OS must use docker-compose.yml in bridge mode. Camera
# discovery uses explicit IPs or unicast subnet scanning, never host mode.
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

# ── 1. start.sh's Linux default is docker-compose.yml ──
start_test "start.sh Linux case-arm selects docker-compose.yml"
# Parse the case statement; look for the Linux*) arm and check
# what COMPOSE_FILE it assigns. Awk picks the block between
# ``Linux*)`` and the first ``;;``.
linux_arm=$(awk '/Linux\*\)/,/;;/' "${REPO_ROOT}/start.sh")
linux_default=$(echo "$linux_arm" | grep -oE 'COMPOSE_FILE="[^"]+"' | head -1 | sed 's/.*="//; s/"$//')
if [ "$linux_default" = "docker-compose.yml" ]; then
    pass
else
    fail "start.sh Linux default must be docker-compose.yml; got: ${linux_default}"
fi

# ── 2. OPENNVR_COMPOSE_FILE override hook exists ──
# The override remains available for custom Compose overlays.
start_test "OPENNVR_COMPOSE_FILE env-var override hook is present"
if grep -q 'OPENNVR_COMPOSE_FILE' "${REPO_ROOT}/start.sh"; then
    pass
else
    fail "start.sh must honor an OPENNVR_COMPOSE_FILE env var so operators
can select a custom Compose overlay when needed."
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
subjects."
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
