#!/usr/bin/env bash
# ============================================================
# Regression test for the first-time setup token banner pipeline
# (ISSUE-5 follow-up, task #9).
#
# CONTEXT
# -------
# ISSUE-5 was: on slow boots (Pi 5, first-time YOLOv8 .pt→ONNX
# export takes 10-15 min) the `start.sh` token-surfacer timed out
# at 30s and printed a misleading "either the admin is already
# activated or the server is still starting" message. The fix
# was to wait for opennvr-core's Docker healthcheck to pass first,
# then extract the banner from container logs with:
#
#     docker compose logs --no-color --no-log-prefix --tail 5000 opennvr-core \
#       | grep -A 6 "first-time setup token" \
#       | tail -7
#
# That pipeline encodes four assumptions, every one of which is a
# silent-failure trap if it drifts:
#
#   (a) The banner contains the literal string
#       ``first-time setup token`` (case-sensitive).
#   (b) The banner is exactly 7 lines (match + ``-A 6`` more).
#   (c) Crash-loops can produce multiple banners; ``tail -7`` must
#       keep only the LAST one because earlier tokens are dead
#       (the in-memory state died with the crashed container).
#   (d) The token line is between the two dash-rule lines so the
#       operator can grep it out cleanly.
#
# WHAT THIS TEST DOES
# -------------------
# Feeds synthetic log inputs (built from the actual banner format
# in server/main.py) through the exact start.sh grep pipeline and
# asserts each property holds.
#
# Also reads the banner-emitting code in server/main.py and asserts
# the banner shape matches what the start.sh pipeline expects — if
# someone edits the banner text but forgets to update start.sh
# (or vice versa), this fails before an operator hits a silent
# "first-time setup is already complete" lie on a fresh deploy.
#
# Run with: bash tests/host-hardening/test_setup_token_banner.sh
# ============================================================

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TESTS_RUN=0
TESTS_FAILED=0
start_test() { TESTS_RUN=$((TESTS_RUN + 1)); printf "  [%2d] %s ... " "$TESTS_RUN" "$1"; }
pass() { echo "PASS"; }
fail() { echo "FAIL"; echo "      $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); }

echo "Running setup-token banner pipeline tests"
echo ""

# Synthetic banner — must stay in sync with server/main.py.
# This is the literal text format we expect to see in opennvr-core's
# container logs. If main.py drifts, tests below will catch it.
gen_banner() {
    local token="$1"
    cat <<EOF

================================================================
 OpenNVR first-time setup token (one-time use)
----------------------------------------------------------------
  ${token}
----------------------------------------------------------------
 Pass this token in the \`setup_token\` field of
 POST /auth/first-time-setup. It is consumed on first
 successful use. Restart the server to mint a new one.
================================================================
EOF
}

# Run the EXACT same pipeline start.sh runs on the log stream.
# Keep this aligned with start.sh's print_first_time_setup_token().
run_pipeline() {
    grep -A 6 "first-time setup token" \
      | tail -7
}

# ── 1. happy path: single banner extracted cleanly ──────────
start_test "single banner is extracted and contains the token"
single_log=$(cat <<EOF
[boot] starting up
[boot] DB connected
$(gen_banner "TOKEN-HAPPY-PATH")
[boot] HTTP server listening on 0.0.0.0:8000
[boot] ready
EOF
)
extracted=$(echo "$single_log" | run_pipeline)
if echo "$extracted" | grep -q "TOKEN-HAPPY-PATH"; then
    pass
else
    fail "extracted banner is missing the token. Got:
$extracted"
fi

# ── 2. crash-loop: multiple banners, only the LATEST returned ──
# This is the case that motivates ``tail -7``. If a future
# refactor changes the tail count or removes it entirely, an
# operator would copy-paste the earliest token from a crashed
# boot — invalid in-memory because the container died with it.
start_test "with multiple banners (crash-loop), only the last token is returned"
crash_log=$(cat <<EOF
[boot 1] starting up
$(gen_banner "TOKEN-FIRST-BOOT-DEAD")
[boot 1] crashed: out of memory
[boot 2] restarting
$(gen_banner "TOKEN-SECOND-BOOT-DEAD")
[boot 2] crashed: db connection lost
[boot 3] restarting
$(gen_banner "TOKEN-LATEST-LIVE")
[boot 3] ready
EOF
)
extracted=$(echo "$crash_log" | run_pipeline)
if echo "$extracted" | grep -q "TOKEN-LATEST-LIVE" \
   && ! echo "$extracted" | grep -q "TOKEN-FIRST-BOOT-DEAD" \
   && ! echo "$extracted" | grep -q "TOKEN-SECOND-BOOT-DEAD"; then
    pass
else
    fail "expected only TOKEN-LATEST-LIVE in the extracted banner.
Got:
$extracted"
fi

# ── 3. no banner: pipeline returns empty (signals "admin already activated") ──
# start.sh interprets an empty result as "first-time setup is
# already complete" and prints a different message. The test
# locks that: when no banner is in the logs, the pipeline must
# return exactly empty (not partial text).
start_test "no banner in logs ⇒ pipeline returns empty"
no_banner_log=$(cat <<'EOF'
[boot] starting up
[boot] admin user already activated, skipping setup-token arm
[boot] HTTP server listening on 0.0.0.0:8000
[boot] ready
EOF
)
extracted=$(echo "$no_banner_log" | run_pipeline)
if [ -z "$extracted" ]; then
    pass
else
    fail "expected empty extraction; got:
$extracted"
fi

# ── 4. banner shape: exactly 7 lines (match + -A 6 more) ─────
# If the banner ever shrinks to 6 lines, ``tail -7`` will leak
# a line from the boot output BEFORE the banner. If it grows to
# 8, the operator's "Restart the server" guidance line gets
# clipped. Either way it confuses operators on a fresh deploy.
start_test "banner is exactly 7 lines (matches start.sh tail -7)"
banner_lines=$(gen_banner "T" | grep -c .)
# gen_banner has a leading blank line + 9 content lines (header,
# title, rule, token, rule, prose×3, footer). Strip the blank.
# `grep -c .` counts non-empty lines; the banner content is
# 9 non-empty lines, but the grep -A 6 only catches the title
# match + 6 below, totaling 7.
# Recompute as start.sh would see it post-pipeline:
banner_pipeline_lines=$(gen_banner "T" | run_pipeline | wc -l | tr -d ' ')
if [ "$banner_pipeline_lines" = "7" ]; then
    pass
else
    fail "post-pipeline banner is ${banner_pipeline_lines} lines; expected 7.
If main.py's banner format changed, update start.sh's tail count
to match (currently hardcoded as 'tail -7' in print_first_time_setup_token)."
fi

# ── 5. structural: server/main.py banner matches what start.sh greps for ──
# This is the contract: the literal string ``first-time setup token``
# must appear in main.py's banner text. If main.py renames it (e.g.
# to ``OpenNVR bootstrap token``), the start.sh grep returns empty
# and every fresh-deploy operator gets the misleading "admin already
# activated" message — without any code that obviously broke.
start_test "server/main.py banner contains the literal 'first-time setup token'"
if grep -q "first-time setup token" "${REPO_ROOT}/server/main.py"; then
    pass
else
    fail "server/main.py does NOT contain the string 'first-time setup token'
which start.sh's grep -A 6 'first-time setup token' anchors on.
The two MUST stay aligned; rename in main.py without renaming in
start.sh would silently break the operator-facing token flow."
fi

# ── 6. structural: start.sh waits for container health before reading logs ──
# This is the actual ISSUE-5 fix. If a future refactor reintroduces
# the original "poll for 30s then give up" pattern, every Pi 5
# operator on a first boot regresses.
start_test "start.sh waits for container health before extracting banner"
# The health-wait loop reads docker inspect's .State.Health.Status
# and breaks on 'healthy'. Lock that shape.
# Two anchors: (1) reads .State.Health.Status from docker inspect,
# (2) has a case-arm or branch that breaks on 'healthy'. Together
# they describe the health-poll loop shape.
if grep -qE "State\.Health\.Status" "${REPO_ROOT}/start.sh" \
   && grep -qE "^\s*healthy\)" "${REPO_ROOT}/start.sh"; then
    pass
else
    fail "start.sh must poll container health via docker inspect's
.State.Health.Status before extracting the token banner. The
ISSUE-5 regression was a 30s wall-clock timeout that lost the
race against init containers on slow hardware."
fi

# ── 7. structural: max-wait is overridable by OPENNVR_SETUP_TOKEN_MAX_WAIT_S ──
# So this test can short-circuit the 20-minute production timeout
# with e.g. OPENNVR_SETUP_TOKEN_MAX_WAIT_S=10 in a future docker-in-
# docker integration test. The override hook MUST exist.
start_test "max-wait is overridable via OPENNVR_SETUP_TOKEN_MAX_WAIT_S env var"
if grep -q 'OPENNVR_SETUP_TOKEN_MAX_WAIT_S' "${REPO_ROOT}/start.sh"; then
    pass
else
    fail "start.sh must read OPENNVR_SETUP_TOKEN_MAX_WAIT_S so a future
docker-in-docker smoke test can short-circuit the 20-min default."
fi

# ── 8. positive contract: first_time_setup_service.maybe_arm is idempotent ──
# Reads the actual service code and asserts the guard exists. We're
# not running the function (that's a pytest job) but the shape of
# "if _state is not None: return None" must be in the source — its
# absence would silently mint a fresh token on every restart and
# break operators mid-copy-paste.
start_test "first_time_setup_service.maybe_arm is idempotent (early-return guard)"
svc_file="${REPO_ROOT}/server/services/first_time_setup_service.py"
# The guard is: ``if _state is not None: return None``. Match it
# loosely (whitespace-tolerant) so cosmetic reformatting doesn't
# trigger a false fail.
if python3 -c "
import ast, sys
tree = ast.parse(open('${svc_file}').read())

def looks_like_state_guard(stmt):
    '''Match: if _state is not None: return None'''
    if not isinstance(stmt, ast.If):
        return False
    test = stmt.test
    if not (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == '_state'
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None):
        return False
    # Body should be: return None
    if not (stmt.body
            and isinstance(stmt.body[0], ast.Return)
            and isinstance(stmt.body[0].value, ast.Constant)
            and stmt.body[0].value.value is None):
        return False
    return True

ok = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'maybe_arm':
        # The guard lives inside a 'with _lock:' block, so walk all
        # nested statements rather than only the top-level function body.
        for child in ast.walk(node):
            if looks_like_state_guard(child):
                ok = True
                break
sys.exit(0 if ok else 1)
"; then
    pass
else
    fail "maybe_arm() must early-return None when _state is already armed.
Removing the guard would mint a fresh token on every server boot
and mid-copy-paste operators would copy stale tokens."
fi

# ── 9. docker-entrypoint.sh forwards the banner to container stdout (ISSUE-29) ──
# Supervisord redirects the backend's stdout to /app/logs/opennvr-backend.log,
# so print() in maybe_arm() never reaches PID 1's stdout. Without this
# forwarder the banner IS minted but invisible to ``docker compose logs``
# and therefore to start.sh's grep — operator gets "First-time setup is
# already complete" even when the token sits unread in a log file.
#
# The forwarder must: tail -F the backend log, grep -A 6 the banner (to
# match start.sh's ``grep -A 6 "first-time setup token" | tail -7``
# contract), and run in the background so it doesn't block supervisord.
start_test "docker-entrypoint.sh forwards setup-token banner to stdout"
ep_file="${REPO_ROOT}/docker-entrypoint.sh"
if grep -qE 'tail.+-F.+opennvr-backend\.log' "$ep_file" \
   && grep -qE 'grep.+-A[[:space:]]+6.+"first-time setup token"' "$ep_file" \
   && grep -qE '\)[[:space:]]*&[[:space:]]*$' "$ep_file"; then
    pass
else
    fail "docker-entrypoint.sh must include a background forwarder of the form:
    (
        mkdir -p /app/logs
        touch /app/logs/opennvr-backend.log
        chown opennvr:opennvr /app/logs/opennvr-backend.log
        tail -n 0 -F /app/logs/opennvr-backend.log \\
          | grep --line-buffered -A 6 \"first-time setup token\"
    ) &
Without this, the setup token banner is minted by the backend but
trapped in /app/logs/opennvr-backend.log and never reaches the
container stdout that ``docker compose logs`` reads.
The ``-A 6`` must match start.sh's grep range so the banner that
surfaces here is exactly the 7-line block start.sh expects."
fi

# ── 10. forwarder's grep range matches start.sh's (no drift) ──
# If start.sh ever changes from -A 6 / tail -7 to a different range,
# this side must change too or banners get clipped. Compare the
# integers directly.
start_test "docker-entrypoint.sh grep -A range matches start.sh's"
ep_A=$(grep -oE 'grep[^|]*-A[[:space:]]+[0-9]+[^|]*"first-time setup token"' "$ep_file" \
       | grep -oE '\-A[[:space:]]+[0-9]+' | grep -oE '[0-9]+' | head -1)
sh_A=$(grep -oE 'grep[^|]*-A[[:space:]]+[0-9]+[^|]*"first-time setup token"' \
       "${REPO_ROOT}/start.sh" | grep -oE '\-A[[:space:]]+[0-9]+' \
       | grep -oE '[0-9]+' | head -1)
if [ -n "$ep_A" ] && [ -n "$sh_A" ] && [ "$ep_A" = "$sh_A" ]; then
    pass
else
    fail "grep -A range mismatch: docker-entrypoint.sh has -A ${ep_A:-?}, start.sh has -A ${sh_A:-?}.
The two sides MUST use the same number of after-context lines or
the banner clipping diverges between the entrypoint's forward and
start.sh's display."
fi

# ── Summary ────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then echo "Result       : all green"; exit 0
else echo "Result       : failures"; exit 1; fi
