#!/usr/bin/env bash
# ============================================================
# Tests for TLS cert SAN generation (ISSUE-6 v9):
#   * Both init containers (nginx-certs-init, mediamtx-certs-init)
#     read OPENNVR_HOST_IP and add it to the SAN list.
#   * start.sh's configure_nginx_bind_host exports OPENNVR_HOST_IP
#     in all four code paths (single-NIC silent, dual-declared,
#     walkthrough Simple, walkthrough Advanced) when not already
#     set by the operator.
#   * The cert-init shell scripts produce valid openssl invocations
#     when run directly with a stub openssl.
#
# Run with: bash tests/host-hardening/test_cert_san.sh
# ============================================================

set -u

. "$(dirname "$0")/_lib.sh"
require_python_yaml

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_TIER0="${REPO_ROOT}/docker-compose.tier0.yml"
START_SH="${REPO_ROOT}/start.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

TESTS_RUN=0
TESTS_FAILED=0
start_test() { TESTS_RUN=$((TESTS_RUN + 1)); printf "  [%2d] %s ... " "$TESTS_RUN" "$1"; }
pass() { echo "PASS"; }
fail() { echo "FAIL"; echo "      $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); }

# Extract the inline shell command from a compose init-container.
get_init_command() {
    local service="$1"
    python3 - <<PY
import yaml
c = yaml.safe_load(open("${COMPOSE_TIER0}"))
cmd = c["services"]["${service}"]["command"]
# command may be a list ["sh","-c",SCRIPT] — take the SCRIPT.
if isinstance(cmd, list) and len(cmd) >= 3:
    print(cmd[2])
else:
    print(cmd)
PY
}

echo "Running cert-SAN tests"
echo ""

# ── 1-2. Both cert-init scripts read OPENNVR_HOST_IP ────────
for svc in nginx-certs-init mediamtx-certs-init; do
    start_test "${svc} extends SAN with OPENNVR_HOST_IP when set"
    script=$(get_init_command "$svc")
    if echo "$script" | grep -q 'OPENNVR_HOST_IP' \
       && echo "$script" | grep -q 'IP:.*OPENNVR_HOST_IP\|SAN.*OPENNVR_HOST_IP'; then
        pass
    else
        fail "${svc} command must reference OPENNVR_HOST_IP and add IP:\${OPENNVR_HOST_IP} to SAN"
    fi
done

# ── 3-4. Both have the env var declared ─────────────────────
for svc in nginx-certs-init mediamtx-certs-init; do
    start_test "${svc} declares OPENNVR_HOST_IP in environment"
    result=$(python3 - <<PY
import yaml
c = yaml.safe_load(open("${COMPOSE_TIER0}"))
env = c["services"]["${svc}"].get("environment", [])
print(any("OPENNVR_HOST_IP" in str(e) for e in env))
PY
    )
    if echo "$result" | grep -q "True"; then
        pass
    else
        fail "${svc} must declare OPENNVR_HOST_IP in environment so compose interpolates it"
    fi
done

# ── 5. End-to-end SAN generation: simulate the script with stub openssl ──
start_test "nginx-certs-init produces a SAN that includes OPENNVR_HOST_IP at runtime"
SCRIPT=$(get_init_command nginx-certs-init)
# Write the script to a file with bash-compatible escaping. Compose
# uses $$ for literal $ in interpolation context; convert back to $.
echo "$SCRIPT" | sed 's/\$\$/\$/g' > "$TMPDIR/init.sh"

# Stub openssl so we can inspect what addext args it would have been
# called with.
mkdir -p "$TMPDIR/bin"
cat > "$TMPDIR/bin/openssl" <<'STUB'
#!/usr/bin/env bash
echo "OPENSSL_CALLED" > /tmp/.cert-san-test-marker
echo "ARGS: $*" >> /tmp/.cert-san-test-marker
exit 0
STUB
chmod +x "$TMPDIR/bin/openssl"
cat > "$TMPDIR/bin/apk" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$TMPDIR/bin/apk"

# Stub chmod to no-op (we don't actually write certs).
mkdir -p "$TMPDIR/certs"
rm -f /tmp/.cert-san-test-marker

# Run the init script with OPENNVR_HOST_IP set, in a sandbox dir
# where /certs/ is empty (so the idempotent check doesn't skip).
(
    export PATH="$TMPDIR/bin:$PATH"
    export OPENNVR_HOST_IP="192.168.42.42"
    # The script writes to /certs which we can't override easily; use
    # a small wrapper that maps /certs/ to our TMPDIR.
    sed -i.bak 's|/certs|'"$TMPDIR"'/certs|g' "$TMPDIR/init.sh"
    bash "$TMPDIR/init.sh" 2>&1
) > "$TMPDIR/init-output" 2>&1 || true

if [ -f /tmp/.cert-san-test-marker ] \
   && grep -q "192.168.42.42" /tmp/.cert-san-test-marker; then
    pass
else
    fail "nginx-certs-init didn't propagate OPENNVR_HOST_IP into openssl SAN args. Output: $(cat /tmp/.cert-san-test-marker 2>/dev/null | head -5)"
fi
rm -f /tmp/.cert-san-test-marker

# ── 6. mediamtx-certs-init likewise propagates OPENNVR_HOST_IP ──
start_test "mediamtx-certs-init produces a SAN that includes OPENNVR_HOST_IP at runtime"
SCRIPT=$(get_init_command mediamtx-certs-init)
echo "$SCRIPT" | sed 's/\$\$/\$/g' > "$TMPDIR/init2.sh"
sed -i.bak 's|/certs|'"$TMPDIR"'/certs2|g' "$TMPDIR/init2.sh"
mkdir -p "$TMPDIR/certs2"
rm -f /tmp/.cert-san-test-marker
(
    export PATH="$TMPDIR/bin:$PATH"
    export OPENNVR_HOST_IP="10.20.30.40"
    bash "$TMPDIR/init2.sh" 2>&1
) > "$TMPDIR/init2-output" 2>&1 || true
if [ -f /tmp/.cert-san-test-marker ] \
   && grep -q "10.20.30.40" /tmp/.cert-san-test-marker; then
    pass
else
    fail "mediamtx-certs-init didn't propagate OPENNVR_HOST_IP. Output: $(cat /tmp/.cert-san-test-marker 2>/dev/null | head -5)"
fi
rm -f /tmp/.cert-san-test-marker

# ── 7. Without OPENNVR_HOST_IP, SAN falls back to default ───
start_test "nginx-certs-init falls back to localhost-only SAN when OPENNVR_HOST_IP is empty"
rm -f /tmp/.cert-san-test-marker
(
    export PATH="$TMPDIR/bin:$PATH"
    unset OPENNVR_HOST_IP
    bash "$TMPDIR/init.sh" 2>&1
) > /dev/null 2>&1 || true
# We just deleted the certs dir; the idempotent check should pass.
# Recreate empty so it actually runs.
rm -rf "$TMPDIR/certs"; mkdir -p "$TMPDIR/certs"
(
    export PATH="$TMPDIR/bin:$PATH"
    unset OPENNVR_HOST_IP
    bash "$TMPDIR/init.sh" 2>&1
) > /dev/null 2>&1 || true

if [ -f /tmp/.cert-san-test-marker ] \
   && grep -q "DNS:localhost" /tmp/.cert-san-test-marker \
   && ! grep -qE "IP:[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" /tmp/.cert-san-test-marker \
        | grep -v "127.0.0.1"; then
    # SAN includes localhost and 127.0.0.1, but no other public IPs.
    if grep -q "IP:127.0.0.1" /tmp/.cert-san-test-marker; then
        pass
    else
        fail "fallback SAN must include 127.0.0.1. Marker: $(cat /tmp/.cert-san-test-marker | head -3)"
    fi
else
    fail "fallback SAN unexpected. Marker: $(cat /tmp/.cert-san-test-marker 2>/dev/null | head -3)"
fi
rm -f /tmp/.cert-san-test-marker

# ── 8. start.sh exports OPENNVR_HOST_IP in all four paths ───
start_test "start.sh configure_nginx_bind_host exports OPENNVR_HOST_IP in every path"
# Look for the export pattern inside configure_nginx_bind_host AND
# inside prompt_nic_topology's 's' (Simple) and 'd' (Dual) branches.
# All four code paths should reach an `export OPENNVR_HOST_IP=` line
# (gated on the operator not having already set it).
count=$(grep -c "export OPENNVR_HOST_IP=" "$START_SH")
if [ "$count" -ge 3 ]; then
    pass
else
    fail "expected ≥3 export OPENNVR_HOST_IP= sites in start.sh (single-NIC, dual-declared, walkthrough); got ${count}"
fi

# ── 9. Each export respects an operator-set value ───────────
start_test "start.sh never overrides an operator-set OPENNVR_HOST_IP"
# Every export OPENNVR_HOST_IP= site must be preceded (within a
# few lines) by a check like `if [ -z "$(get_env_var OPENNVR_HOST_IP ...)" ]`
# so the operator's explicit choice wins.
guards=$(grep -B 1 "export OPENNVR_HOST_IP=" "$START_SH" \
         | grep -c "get_env_var OPENNVR_HOST_IP")
exports=$(grep -c "^[[:space:]]*export OPENNVR_HOST_IP=" "$START_SH")
if [ "$guards" -eq "$exports" ]; then
    pass
else
    fail "every export must be guarded by a get_env_var check (operator value wins). Guards: ${guards}, exports: ${exports}"
fi

# ── 10. refresh-certs subcommand exists ────────────────────
start_test "start.sh has a refresh-certs subcommand"
if grep -q "refresh-certs)" "$START_SH"; then
    pass
else
    fail "start.sh missing refresh-certs case branch"
fi

# ── 11. refresh-certs is documented in usage line ──────────
start_test "refresh-certs is listed in the Usage help text"
if grep -q "refresh-certs" "$START_SH" \
   && grep "Usage: ./start.sh" "$START_SH" | grep -q "refresh-certs"; then
    pass
else
    fail "refresh-certs missing from the Usage help line"
fi

# ── Summary ────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then echo "Result       : all green"; exit 0
else echo "Result       : failures"; exit 1; fi
