#!/usr/bin/env bash
# ============================================================
# Tests for scripts/apply-camera-vlan-hardening.sh — the script
# that installs the nftables forward-drop rules between the
# camera-network NIC and the uplink NIC (ISSUE-6 v3).
#
# Scope of this test file (static / dry-run):
#   * Generated `inet opennvr-vlan` table has the right shape:
#     table name, chain hook, priority, policy, log prefixes,
#     and bidirectional forward-drop rules tied to the operator's
#     declared interface names.
#   * The script refuses obviously-broken input (same iface for
#     both sides, non-existent iface, missing required args)
#     before any sudo prompt happens.
#   * The dry-run path prints the commands it WOULD run without
#     actually running them.
#
# Out of scope (deferred):
#   * Kernel-level packet-drop verification. That requires a
#     test rig with network namespaces, packet generators, and
#     nft loaded — tracked separately. Static template tests
#     give us regression coverage for the generated ruleset
#     shape, which is where almost all human errors land.
#
# Run with:
#   bash tests/host-hardening/test_nftables_template.sh
# Or directly: ./tests/host-hardening/test_nftables_template.sh
# ============================================================

set -u

# Resolve repo root from this file so the tests run from anywhere.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPLY_SCRIPT="${REPO_ROOT}/scripts/apply-camera-vlan-hardening.sh"
REVERT_SCRIPT="${REPO_ROOT}/scripts/revert-camera-vlan-hardening.sh"

# A throwaway working directory so we don't pollute the repo with
# host-hardening/ artifacts from --dry-run.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Stub `ip` that pretends eth0 and eth1 exist so the apply script's
# "does this interface exist?" check passes without root.
STUB_BIN="${TMPDIR}/bin"
mkdir -p "$STUB_BIN"
cat > "${STUB_BIN}/ip" <<'STUB_EOF'
#!/bin/bash
if [ "$1" = "link" ] && [ "$2" = "show" ]; then
    case "$3" in
        eth0|eth1|eth0.10|eth0.20) echo "1: $3: <BROADCAST,MULTICAST,UP>"; exit 0 ;;
        *) echo "Device \"$3\" does not exist." >&2; exit 1 ;;
    esac
fi
exit 0
STUB_EOF
chmod +x "${STUB_BIN}/ip"
# Stub `nft` so the script's "is nftables installed?" check passes
# even on hosts without nftables (e.g. CI / macOS).
cat > "${STUB_BIN}/nft" <<'STUB_EOF'
#!/bin/bash
# Just enough to satisfy `command -v nft` — the dry-run path
# never actually invokes us.
exit 0
STUB_EOF
chmod +x "${STUB_BIN}/nft"
export PATH="${STUB_BIN}:${PATH}"

# ── Tiny assertion framework ─────────────────────────────────
TESTS_RUN=0
TESTS_FAILED=0
CURRENT_TEST=""

start_test() {
    CURRENT_TEST="$1"
    TESTS_RUN=$((TESTS_RUN + 1))
    printf "  [%2d] %s ... " "$TESTS_RUN" "$CURRENT_TEST"
}

pass() {
    echo "PASS"
}

fail() {
    echo "FAIL"
    echo "      $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

assert_contains() {
    local needle="$1" haystack="$2" message="${3:-content missing}"
    if echo "$haystack" | grep -qF -- "$needle"; then
        return 0
    fi
    fail "${message}: expected to find '${needle}'"
    return 1
}

assert_not_contains() {
    local needle="$1" haystack="$2" message="${3:-content present that should not be}"
    if ! echo "$haystack" | grep -qF -- "$needle"; then
        return 0
    fi
    fail "${message}: should not contain '${needle}'"
    return 1
}

# ── Helper: run the apply script in dry-run, capture output  ─
run_apply() {
    local out
    # OPENNVR_HARDEN_DIR redirects the apply script's output into the
    # test's tmpdir so we don't pollute the repo's host-hardening/.
    out=$(OPENNVR_HARDEN_DIR="${TMPDIR}/host-hardening" \
          bash "$APPLY_SCRIPT" "$@" 2>&1)
    local rc=$?
    echo "$out"
    return $rc
}

# Render the generated nftables file so tests can inspect it.
render_template() {
    local cam="$1" mgmt="$2"
    run_apply --camera-iface "$cam" --mgmt-iface "$mgmt" --dry-run >/dev/null
    cat "${TMPDIR}/host-hardening/opennvr-vlan.nft"
}

echo "Running nftables template tests against ${APPLY_SCRIPT}"
echo ""

# ── Test 1: file exists and is executable ────────────────────
start_test "apply script is executable"
if [ -x "$APPLY_SCRIPT" ]; then
    pass
else
    fail "${APPLY_SCRIPT} is not executable"
fi

start_test "revert script is executable"
if [ -x "$REVERT_SCRIPT" ]; then
    pass
else
    fail "${REVERT_SCRIPT} is not executable"
fi

# ── Test: shape of generated nftables template ───────────────
start_test "generated table uses dedicated name 'inet opennvr-vlan'"
template=$(render_template eth0 eth1)
if assert_contains "table inet opennvr-vlan" "$template" "wrong table name"; then
    pass
fi

start_test "generated chain hooks forward path"
if assert_contains "type filter hook forward priority" "$template" "missing forward hook"; then
    pass
fi

start_test "chain priority is high (negative number → early in chain)"
# We use priority -150 so our drop fires before other tables can
# accept. The exact number is part of the contract.
if assert_contains "priority -150" "$template" "priority must be -150"; then
    pass
fi

start_test "default policy is accept (we only add drop rules, no global deny)"
if assert_contains "policy accept" "$template" "policy should be accept"; then
    pass
fi

start_test "blocks forwarding from camera NIC to uplink NIC"
if assert_contains 'iifname "eth0" oifname "eth1"' "$template" "missing cam→mgmt drop"; then
    if assert_contains "drop" "$template" "cam→mgmt rule missing drop verdict"; then
        pass
    fi
fi

start_test "blocks forwarding from uplink NIC to camera NIC (bidirectional)"
if assert_contains 'iifname "eth1" oifname "eth0"' "$template" "missing mgmt→cam drop"; then
    pass
fi

start_test "drop events are tagged with [opennvr-vlan ...] log prefix for greppability"
if assert_contains "opennvr-vlan drop cam->mgmt" "$template" "missing cam→mgmt log prefix"; then
    if assert_contains "opennvr-vlan drop mgmt->cam" "$template" "missing mgmt→cam log prefix"; then
        pass
    fi
fi

start_test "template references no other interface besides the declared ones"
# A regex tripwire: any iifname/oifname that isn't eth0 or eth1
# would be a generation bug.
bad_iface=$(echo "$template" \
    | grep -oE '(iifname|oifname) "[^"]*"' \
    | grep -vE '"eth0"$|"eth1"$' || true)
if [ -z "$bad_iface" ]; then
    pass
else
    fail "unexpected interface in template: ${bad_iface}"
fi

# ── Test: interface name handling ────────────────────────────
start_test "supports VLAN sub-interfaces like eth0.10 / eth0.20"
vlan_template=$(render_template eth0.10 eth0.20)
if assert_contains 'iifname "eth0.10" oifname "eth0.20"' "$vlan_template" "VLAN ifaces missing"; then
    pass
fi

# ── Test: input validation ───────────────────────────────────
start_test "refuses when --camera-iface is missing"
out=$(run_apply --mgmt-iface eth1 --dry-run)
rc=$?
if [ "$rc" -ne 0 ] && echo "$out" | grep -q "required"; then
    pass
else
    fail "expected exit-1 with 'required' in output; got rc=$rc, output: ${out:0:200}"
fi

start_test "refuses when --mgmt-iface is missing"
out=$(run_apply --camera-iface eth0 --dry-run)
rc=$?
if [ "$rc" -ne 0 ] && echo "$out" | grep -q "required"; then
    pass
else
    fail "expected exit-1 with 'required' in output; got rc=$rc"
fi

start_test "refuses when camera and uplink are the same interface"
out=$(run_apply --camera-iface eth0 --mgmt-iface eth0 --dry-run)
rc=$?
if [ "$rc" -ne 0 ] && echo "$out" | grep -qi "defeats isolation\|same"; then
    pass
else
    fail "expected exit-1 with 'same'/'defeats isolation'; got rc=$rc"
fi

start_test "refuses when camera interface doesn't exist on this host"
out=$(run_apply --camera-iface bogus0 --mgmt-iface eth1 --dry-run)
rc=$?
if [ "$rc" -ne 0 ] && echo "$out" | grep -q "does not exist"; then
    pass
else
    fail "expected exit-1 with 'does not exist'; got rc=$rc"
fi

start_test "refuses when uplink interface doesn't exist on this host"
out=$(run_apply --camera-iface eth0 --mgmt-iface bogus1 --dry-run)
rc=$?
if [ "$rc" -ne 0 ] && echo "$out" | grep -q "does not exist"; then
    pass
else
    fail "expected exit-1 with 'does not exist'; got rc=$rc"
fi

# ── Test: dry-run prints commands but doesn't execute ────────
start_test "dry-run prints the sudo commands it would execute"
out=$(run_apply --camera-iface eth0 --mgmt-iface eth1 --dry-run)
if assert_contains "sudo nft -f" "$out" "dry-run didn't print sudo nft command"; then
    if assert_contains "Dry-run requested" "$out" "missing dry-run notice"; then
        pass
    fi
fi

start_test "dry-run does NOT prompt for sudo or apply rules"
# If --dry-run accidentally fell through to the apply path, we'd
# see a 'Apply now?' prompt or 'Applying' status. Neither should
# appear in dry-run output.
out=$(run_apply --camera-iface eth0 --mgmt-iface eth1 --dry-run)
if assert_not_contains "Apply now?" "$out" "dry-run leaked apply prompt"; then
    if assert_not_contains "Applying opennvr-vlan" "$out" "dry-run leaked apply action"; then
        pass
    fi
fi

start_test "dry-run shows the operator the generated template inline"
out=$(run_apply --camera-iface eth0 --mgmt-iface eth1 --dry-run)
if assert_contains "table inet opennvr-vlan" "$out" "template not shown in dry-run output"; then
    pass
fi

# ── Test: trust-boundary properties ──────────────────────────
start_test "table is isolated (does NOT modify the default filter table)"
template=$(render_template eth0 eth1)
# We must NOT touch 'table inet filter' or 'table ip filter' — that
# would clobber operator's existing UFW / firewalld / iptables rules.
if assert_not_contains "table inet filter" "$template" "would clobber default filter table"; then
    if assert_not_contains "table ip filter" "$template" "would clobber legacy filter table"; then
        pass
    fi
fi

start_test "template does NOT touch DNS, routing, or input/output chains"
# Out-of-scope per the script's contract — those would risk locking
# the operator out (V-016/V-017 will handle them with their own
# consent flow when they ship).
if assert_not_contains "type filter hook input" "$template" "input hook is out of scope"; then
    if assert_not_contains "type filter hook output" "$template" "output hook is out of scope"; then
        if assert_not_contains "ip route" "$template" "routing changes are out of scope"; then
            pass
        fi
    fi
fi

start_test "template carries a header pointing operators at the revert path"
out=$(render_template eth0 eth1)
if assert_contains "revert-camera-vlan-hardening.sh" "$out" "missing revert pointer in template header"; then
    pass
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then
    echo "Result       : ✓ all green"
    exit 0
else
    echo "Result       : ✗ failures"
    exit 1
fi
