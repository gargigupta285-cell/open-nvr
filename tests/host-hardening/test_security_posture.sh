#!/usr/bin/env bash
# ============================================================
# Tests for print_security_posture in start.sh — the function
# that flags security limitations every ./start.sh up/build
# (ISSUE-6 v5).
#
# Contract: print to stderr ONLY when there is something to
# flag. Silent when the deployment is fully locked down.
#
# Each test sets up a controlled .env + host-hardening/ state
# in a tmpdir, sources the function from start.sh, runs it,
# and asserts what was (or was not) written to stderr.
#
# Run with: bash tests/host-hardening/test_security_posture.sh
# ============================================================

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
START_SH="${REPO_ROOT}/start.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Source only the two helpers we need so we don't drag in the
# rest of start.sh (NIC detection, prompt, harden invocation).
# Colours empty so output is greppable.
load_helpers() {
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BRIGHT_CYAN=''
    GRAY=''; WHITE=''; NC=''
    eval "$(awk '/^get_env_var\(\)/,/^}/' "$START_SH")"
    eval "$(awk '/^print_security_posture\(\)/,/^}$/' "$START_SH")"
}

# Tiny assertion framework, same shape as test_nftables_template.
TESTS_RUN=0
TESTS_FAILED=0

start_test() {
    TESTS_RUN=$((TESTS_RUN + 1))
    printf "  [%2d] %s ... " "$TESTS_RUN" "$1"
}

pass() { echo "PASS"; }
fail() {
    echo "FAIL"
    echo "      $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

# Capture the function's stderr to a string for inspection.
capture_posture() {
    (cd "$TMPDIR" && print_security_posture) 2>&1
}

# Reset working state.
reset_state() {
    cd "$TMPDIR"
    rm -rf .env host-hardening
    unset ALLOW_REMOTE_MEDIAMTX
}

# ── Test runner ──────────────────────────────────────────────
load_helpers

echo "Running security-posture tests against ${START_SH}"
echo ""

# ── Test 1: single-LAN fires the trust-mode warning ──────────
start_test "single-LAN mode flags the trust limitation"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
POSTGRES_USER=opennvr_user
LOG_LEVEL=INFO
ENVEOF
out=$(capture_posture)
# Contract: warn that the network is shared AND give a non-tech
# user something actionable (change default passwords — highest-
# impact mitigation anyone can do). The exact wording is allowed
# to evolve; the tests pin to the contract, not the prose.
if echo "$out" | grep -qiE "simple network setup|single.?lan" \
   && echo "$out" | grep -qi "default password"; then
    pass
else
    fail "expected simple-network warning + password tip; got: ${out:0:300}"
fi

# ── Test 2: dual-NIC declared but no hardening fires the kernel warning ──
start_test "dual-NIC without hardening flags the kernel-firewall gap"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
CAMERA_NETWORK_INTERFACE=eth0
MGMT_NETWORK_INTERFACE=eth1
ENVEOF
out=$(capture_posture)
# Contract: warn that kernel-level forward-drop isn't in place AND
# point at the apply script the operator should run.
if echo "$out" | grep -qiE "isolation not enforced|firewall.+not applied|kernel" \
   && echo "$out" | grep -q "apply-camera-vlan-hardening.sh"; then
    pass
else
    fail "expected dual-NIC kernel-firewall warning + apply-script pointer; got: ${out:0:300}"
fi

# ── Test 3: dual-NIC + hardening applied is SILENT ───────────
start_test "dual-NIC + hardening active produces no output"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
CAMERA_NETWORK_INTERFACE=eth0
MGMT_NETWORK_INTERFACE=eth1
ENVEOF
mkdir -p "${TMPDIR}/host-hardening/snap-x"
ln -sfn snap-x "${TMPDIR}/host-hardening/snapshot-active"
out=$(capture_posture)
if [ -z "$(echo "$out" | tr -d '[:space:]')" ]; then
    pass
else
    fail "expected silent output for fully-hardened posture; got: ${out}"
fi

# ── Test 4: legacy ALLOW_REMOTE_MEDIAMTX in .env fires ───────
start_test "legacy ALLOW_REMOTE_MEDIAMTX in .env is flagged"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
CAMERA_NETWORK_INTERFACE=eth0
MGMT_NETWORK_INTERFACE=eth1
ALLOW_REMOTE_MEDIAMTX=true
ENVEOF
mkdir -p "${TMPDIR}/host-hardening/snap-y"
ln -sfn snap-y "${TMPDIR}/host-hardening/snapshot-active"
out=$(capture_posture)
if echo "$out" | grep -q "ALLOW_REMOTE_MEDIAMTX" \
   && echo "$out" | grep -qiE "ignored|retired"; then
    pass
else
    fail "expected legacy-flag warning; got: ${out:0:200}"
fi

# ── Test 5: legacy ALLOW_REMOTE_MEDIAMTX in shell env fires too ──
start_test "legacy ALLOW_REMOTE_MEDIAMTX in shell env is flagged"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
CAMERA_NETWORK_INTERFACE=eth0
MGMT_NETWORK_INTERFACE=eth1
ENVEOF
mkdir -p "${TMPDIR}/host-hardening/snap-z"
ln -sfn snap-z "${TMPDIR}/host-hardening/snapshot-active"
export ALLOW_REMOTE_MEDIAMTX=true
out=$(capture_posture)
unset ALLOW_REMOTE_MEDIAMTX
if echo "$out" | grep -q "ALLOW_REMOTE_MEDIAMTX"; then
    pass
else
    fail "expected legacy-flag warning from shell env; got: ${out:0:200}"
fi

# ── Test 6: multiple issues stack (single-LAN + legacy flag) ─
start_test "multiple issues stack in one banner"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
POSTGRES_USER=opennvr_user
ALLOW_REMOTE_MEDIAMTX=true
ENVEOF
out=$(capture_posture)
if echo "$out" | grep -qiE "simple network|single.?lan" \
   && echo "$out" | grep -q "ALLOW_REMOTE_MEDIAMTX"; then
    pass
else
    fail "expected both warnings to appear; got: ${out:0:300}"
fi

# ── Test 7: banner header appears only when there ARE warnings ──
start_test "banner header is suppressed in the all-clean case"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
CAMERA_NETWORK_INTERFACE=eth0
MGMT_NETWORK_INTERFACE=eth1
ENVEOF
mkdir -p "${TMPDIR}/host-hardening/snap-clean"
ln -sfn snap-clean "${TMPDIR}/host-hardening/snapshot-active"
out=$(capture_posture)
if echo "$out" | grep -qE "Heads up|Security posture"; then
    fail "all-clean posture should not print the banner header"
else
    pass
fi

# ── Test 8: mitigation pointer always names a runnable next step ──
start_test "every warning includes an actionable mitigation pointer"
reset_state
cat > "${TMPDIR}/.env" <<ENVEOF
POSTGRES_USER=opennvr_user
ENVEOF
out_single=$(capture_posture)

cat > "${TMPDIR}/.env" <<ENVEOF
CAMERA_NETWORK_INTERFACE=eth0
MGMT_NETWORK_INTERFACE=eth1
ENVEOF
out_dual=$(capture_posture)

cat > "${TMPDIR}/.env" <<ENVEOF
ALLOW_REMOTE_MEDIAMTX=true
ENVEOF
out_legacy=$(capture_posture)

if echo "$out_single" | grep -qiE "default password|stronger isolation|dual.?nic" \
   && echo "$out_dual" | grep -qE "apply-camera-vlan-hardening|Fix:" \
   && echo "$out_legacy" | grep -qiE "remove the line|remove it"; then
    pass
else
    fail "every warning must give the user something to do (password tip / fix command / remove line)"
fi

# ── Test 9 (self-review M-1): detect_lan_ip prefers NGINX_BIND_HOST ─
# Bug: on dual-NIC hosts, `hostname -I` might list the camera-LAN
# IP before the uplink IP. detect_lan_ip would then display a URL
# nginx isn't bound to. NGINX_BIND_HOST is the authoritative
# answer once configure_nginx_bind_host has run.
start_test "detect_lan_ip prefers NGINX_BIND_HOST over hostname guess"
# We need access to detect_lan_ip — pull it in alongside the
# helpers we already sourced.
eval "$(awk '/^detect_lan_ip\(\)/,/^}/' "$START_SH")"

# Case A: NGINX_BIND_HOST set to a concrete IP → should win
export NGINX_BIND_HOST="192.168.1.100"
result=$(detect_lan_ip)
if [ "$result" = "192.168.1.100" ]; then
    # Case B: NGINX_BIND_HOST=0.0.0.0 (wildcard) → must fall through
    export NGINX_BIND_HOST="0.0.0.0"
    result_b=$(detect_lan_ip)
    if [ "$result_b" != "0.0.0.0" ]; then
        pass
    else
        fail "0.0.0.0 should NOT be returned by detect_lan_ip (must fall through)"
    fi
else
    fail "expected detect_lan_ip to return NGINX_BIND_HOST (192.168.1.100); got '${result}'"
fi
unset NGINX_BIND_HOST

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then
    echo "Result       : all green"
    exit 0
else
    echo "Result       : failures"
    exit 1
fi
