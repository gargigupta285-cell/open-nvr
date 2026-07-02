#!/usr/bin/env bash
# ============================================================
# Tests that the Docker bridge subnet declared in docker-compose.yml
# (ISSUE-6 v7) is:
#   1. Deterministic (pinned, not auto-assigned by Docker)
#   2. Inside V-015's trust zone (RFC1918 / IPv6 ULA / link-local)
#   3. Operator-overridable via OPENNVR_DOCKER_SUBNET env var
#   4. Doesn't collide with the common consumer LAN ranges
#      home routers default to (192.168.x, 10.x)
#
# ISSUE-17 simplification: the old docker-compose.yml declared
# two separate networks (sentinel_internal + public_uplink) with
# their own OPENNVR_PUBLIC_SUBNET override. docker-compose.yml uses a
# single opennvr_internal network — simpler, easier to reason
# about for V-015 trust-zone classification. docker-compose.yml
# is the direct implementation, so there's only one
# network architecture to validate.
#
# Run with: bash tests/host-hardening/test_docker_subnets.sh
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

echo "Running Docker subnet tests"
echo ""

# ── 1. standard stack compose declares a pinned bridge subnet ────────
start_test "docker-compose.yml pins opennvr_internal subnet"
result=$(python3 - <<PY
import yaml
c = yaml.safe_load(open("${REPO_ROOT}/docker-compose.yml"))
n = c["networks"]["opennvr_internal"]
configs = n.get("ipam", {}).get("config", [])
print(configs[0]["subnet"] if configs else "")
PY
)
if echo "$result" | grep -qE "172\.28\.0\.0/16"; then
    pass
else
    fail "expected opennvr_internal pinned to 172.28/16; got: '${result}'"
fi

# ── 2. The pinned subnet is RFC1918 (inside V-015 trust zone) ──
start_test "opennvr_internal subnet is RFC1918 (inside V-015 trust zone)"
result=$(python3 - <<PY
import yaml, ipaddress
c = yaml.safe_load(open("${REPO_ROOT}/docker-compose.yml"))
ok = True
for name, cfg in c.get("networks", {}).items():
    if not isinstance(cfg, dict): continue
    for entry in cfg.get("ipam", {}).get("config", []):
        sub = entry.get("subnet", "")
        if sub.startswith("\${"):
            sub = sub.split(":-", 1)[1].rstrip("}")
        net = ipaddress.ip_network(sub)
        if not net.is_private:
            print(f"FAIL {name}={sub} (not RFC1918)")
            ok = False
print("OK" if ok else "FAIL")
PY
)
if echo "$result" | tail -1 | grep -q "OK"; then
    pass
else
    fail "non-RFC1918 subnet detected: ${result}"
fi

# ── 3. Override via env var works (interpolation preserved) ──
start_test "OPENNVR_DOCKER_SUBNET interpolation is preserved in docker-compose.yml"
if grep -q '\${OPENNVR_DOCKER_SUBNET:-172.28.0.0/16}' \
        "${REPO_ROOT}/docker-compose.yml"; then
    pass
else
    fail "OPENNVR_DOCKER_SUBNET interpolation missing from docker-compose.yml"
fi

# ── 4. .env.example documents the override var ──────────────
start_test ".env.example documents OPENNVR_DOCKER_SUBNET"
if grep -q "OPENNVR_DOCKER_SUBNET" "${REPO_ROOT}/.env.example"; then
    pass
else
    fail "OPENNVR_DOCKER_SUBNET missing from .env.example"
fi

# ── 5. Default subnet sits outside common home / corporate LANs ──
# Most home routers default to 192.168.0.0/24 or 192.168.1.0/24.
# Common corporate VPN ranges are inside 10/8. 172.28/16 lives in
# 172.16/12 (RFC1918) but well away from those common defaults,
# so the typical install ships without a subnet-collision conflict.
start_test "default subnet (172.28/16) sits outside common consumer LAN ranges"
result=$(python3 - <<PY
import ipaddress
docker = ipaddress.ip_network("172.28.0.0/16")
home_a = ipaddress.ip_network("192.168.0.0/16")
home_b = ipaddress.ip_network("10.0.0.0/8")
print("OK" if not docker.overlaps(home_a) and not docker.overlaps(home_b) else "FAIL")
PY
)
if echo "$result" | grep -q "OK"; then
    pass
else
    fail "default Docker subnet overlaps a common consumer LAN range"
fi

# ── 6. Legacy compose filename is gone ──────────────────────
start_test "legacy docker-compose.tier0.yml is removed"
if [ ! -e "${REPO_ROOT}/docker-compose.tier0.yml" ]; then
    pass
else
    fail "docker-compose.tier0.yml must not coexist with the canonical docker-compose.yml"
fi
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then echo "Result       : all green"; exit 0
else echo "Result       : failures"; exit 1; fi
