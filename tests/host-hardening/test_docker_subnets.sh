#!/usr/bin/env bash
# ============================================================
# Tests that the Docker bridge subnets declared in the compose
# files (ISSUE-6 v7) are:
#   1. Deterministic (pinned, not auto-assigned by Docker)
#   2. Inside V-015's trust zone (RFC1918 / IPv6 ULA / link-local)
#   3. Operator-overridable via OPENNVR_*_SUBNET env vars
#
# Run with: bash tests/host-hardening/test_docker_subnets.sh
# ============================================================

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TESTS_RUN=0
TESTS_FAILED=0
start_test() { TESTS_RUN=$((TESTS_RUN + 1)); printf "  [%2d] %s ... " "$TESTS_RUN" "$1"; }
pass() { echo "PASS"; }
fail() { echo "FAIL"; echo "      $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); }

echo "Running Docker subnet tests"
echo ""

# ── 1. Tier 0 compose declares a pinned bridge subnet ────────
start_test "docker-compose.tier0.yml pins opennvr_internal subnet"
result=$(python3 - <<PY
import yaml
c = yaml.safe_load(open("${REPO_ROOT}/docker-compose.tier0.yml"))
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

# ── 2. Production compose pins both subnets ──────────────────
start_test "docker-compose.yml pins sentinel_internal AND public_uplink"
result=$(python3 - <<PY
import yaml
c = yaml.safe_load(open("${REPO_ROOT}/docker-compose.yml"))
for name in ("sentinel_internal", "public_uplink"):
    n = c["networks"][name]
    cfgs = n.get("ipam", {}).get("config", [])
    print(name, cfgs[0]["subnet"] if cfgs else "MISSING")
PY
)
if echo "$result" | grep -q "sentinel_internal.*172\.28\.0\.0/16" \
   && echo "$result" | grep -q "public_uplink.*172\.29\.0\.0/16"; then
    pass
else
    fail "expected both subnets pinned to 172.28 and 172.29; got: ${result}"
fi

# ── 3. Every pinned subnet is inside V-015's trust zone ──────
start_test "every pinned subnet is RFC1918 (inside V-015 trust zone)"
result=$(python3 - <<PY
import yaml, ipaddress, sys
ok = True
for fn in ("docker-compose.tier0.yml", "docker-compose.yml"):
    c = yaml.safe_load(open(f"${REPO_ROOT}/{fn}"))
    for name, cfg in c.get("networks", {}).items():
        if not isinstance(cfg, dict): continue
        for entry in cfg.get("ipam", {}).get("config", []):
            sub = entry.get("subnet", "")
            if sub.startswith("\${"):
                sub = sub.split(":-", 1)[1].rstrip("}")
            net = ipaddress.ip_network(sub)
            if not net.is_private:
                print(f"FAIL {fn}:{name}={sub} (not RFC1918)")
                ok = False
print("OK" if ok else "FAIL")
PY
)
if echo "$result" | tail -1 | grep -q "OK"; then
    pass
else
    fail "non-RFC1918 subnet detected: ${result}"
fi

# ── 4. Override via env var works (interpolation preserved) ──
start_test "OPENNVR_DOCKER_SUBNET interpolation is preserved in compose"
if grep -q '\${OPENNVR_DOCKER_SUBNET:-172.28.0.0/16}' \
        "${REPO_ROOT}/docker-compose.tier0.yml" \
   && grep -q '\${OPENNVR_DOCKER_SUBNET:-172.28.0.0/16}' \
        "${REPO_ROOT}/docker-compose.yml"; then
    pass
else
    fail "OPENNVR_DOCKER_SUBNET interpolation missing from compose files"
fi

start_test "OPENNVR_PUBLIC_SUBNET interpolation present in production compose"
if grep -q '\${OPENNVR_PUBLIC_SUBNET:-172.29.0.0/16}' \
        "${REPO_ROOT}/docker-compose.yml"; then
    pass
else
    fail "OPENNVR_PUBLIC_SUBNET interpolation missing"
fi

# ── 5. Both env vars are documented in .env.example ──────────
start_test ".env.example documents OPENNVR_DOCKER_SUBNET + OPENNVR_PUBLIC_SUBNET"
if grep -q "OPENNVR_DOCKER_SUBNET" "${REPO_ROOT}/.env.example" \
   && grep -q "OPENNVR_PUBLIC_SUBNET" "${REPO_ROOT}/.env.example"; then
    pass
else
    fail "env override vars missing from .env.example"
fi

# ── 6. Default subnets don't collide with each other ─────────
start_test "sentinel_internal and public_uplink defaults don't overlap"
result=$(python3 - <<PY
import ipaddress
a = ipaddress.ip_network("172.28.0.0/16")
b = ipaddress.ip_network("172.29.0.0/16")
print("OVERLAP" if a.overlaps(b) else "DISTINCT")
PY
)
if echo "$result" | grep -q "DISTINCT"; then
    pass
else
    fail "default subnets overlap: ${result}"
fi

# ── 7. Default subnets don't fall in common consumer LAN ranges ──
start_test "defaults sit outside the common 192.168.x range home routers use"
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

echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then echo "Result       : all green"; exit 0
else echo "Result       : failures"; exit 1; fi
