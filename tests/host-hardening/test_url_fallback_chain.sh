#!/usr/bin/env bash
# ============================================================
# AST contract test for the browser-facing URL fallback chain
# (ISSUE-4 follow-up, originally tracked as ISSUE-4 v2).
#
# CONTEXT
# -------
# Several endpoints in server/routers/streams.py and
# server/routers/recordings.py return MediaMTX URLs to the
# browser as JSON. Those URLs MUST use the "external" fallback
# chain so they're reachable from LAN clients:
#
#     external_url = (
#         settings.mediamtx_external_<x>_url
#         or settings.mediamtx_<x>_url
#         or "http://127.0.0.1:<port>"
#     )
#
# The internal `settings.mediamtx_<x>_url` is the Docker-bridge
# address (`http://mediamtx:8888/...`) — only routable from
# inside the compose network. Returning it to the browser was
# the ISSUE-6 v8 bug that broke recording playback for LAN
# clients. We fixed it in `get_playback_url` then, but two more
# functions in the same file (`get_playback_config` and
# `get_today_segments`) had the same bug — they returned
# `settings.mediamtx_playback_url` directly. Fixed in this PR.
#
# WHAT THIS TEST DOES
# -------------------
# Parses streams.py and recordings.py with `ast`. For each
# attribute reference of the shape `settings.mediamtx_<x>_url`
# (where <x> ∈ {base, hls, rtsps, playback}), checks that it
# appears as the SECOND operand of a `BoolOp(or)` whose FIRST
# operand is `settings.mediamtx_external_<x>_url`. If a bare
# reference appears without that fallback-chain context, the
# test fails and points at the file:line.
#
# Some uses of the internal URL are legitimate (backend-to-
# mediamtx calls that stay on the Docker bridge). Those go in
# the INTERNAL_USE_ALLOWLIST below, scoped per function name.
#
# Run with: bash tests/host-hardening/test_url_fallback_chain.sh
# ============================================================

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TESTS_RUN=0
TESTS_FAILED=0
start_test() { TESTS_RUN=$((TESTS_RUN + 1)); printf "  [%2d] %s ... " "$TESTS_RUN" "$1"; }
pass() { echo "PASS"; }
fail() { echo "FAIL"; echo "      $1"; TESTS_FAILED=$((TESTS_FAILED + 1)); }

echo "Running URL fallback-chain contract tests"
echo ""

# Run the AST analyzer. Outputs one line per violation in the
# form: "<file>:<lineno>:<func>: <attr> not in fallback chain"
# Exit code is the number of violations.
run_ast_check() {
    python3 - "$REPO_ROOT" <<'PY'
import ast
import sys
from pathlib import Path

REPO = Path(sys.argv[1])

# Internal-URL settings attribute → required external counterpart.
# Both come from server/core/config.py. The test fails on uses of
# the LEFT-hand attribute that aren't paired with the RIGHT-hand
# one in a BoolOp(or, ...).
PAIRS = {
    "mediamtx_base_url":     "mediamtx_external_base_url",
    "mediamtx_hls_url":      "mediamtx_external_hls_url",
    "mediamtx_rtsps_url":    "mediamtx_external_rtsps_url",
    "mediamtx_playback_url": "mediamtx_external_playback_url",
}

# Per-line pragma marker. A source line containing this marker is
# exempted from the fallback-chain check. Use ONLY on lines where
# the internal URL is a legitimate server-side call (backend → the
# mediamtx admin API over the Docker bridge), never on lines that
# return the URL to the browser. The marker MUST be followed by a
# rationale describing why this specific line is server-side, so
# the next reviewer doesn't have to reverse-engineer it. Example:
#
#     url = f"{settings.mediamtx_playback_url}/list?path={path}"  # url-internal-ok: server-side LIST to mediamtx admin API
PRAGMA = "url-internal-ok"

def is_settings_attr(node, attr_name):
    """Match `settings.<attr_name>` (Attribute on Name 'settings')."""
    return (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and node.attr == attr_name)

def is_settings_internal_url(node):
    """Match `settings.mediamtx_<x>_url` for x in PAIRS."""
    return (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and node.attr in PAIRS)

def set_parents(tree):
    """ast walks don't track parents by default; attach them."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node
    return tree

def in_fallback_chain(node):
    """
    True if `node` (an Attribute matching settings.mediamtx_<x>_url)
    appears in a BoolOp(or, ...) where the first operand is the
    external counterpart `settings.mediamtx_external_<x>_url`.

    Walks up the parent chain — the BoolOp may be a few levels above
    if the chain is wrapped in parens / a call / assignment.
    """
    expected_external = PAIRS[node.attr]
    cur = node
    # Walk up until we find a BoolOp(or) or hit a statement boundary.
    while hasattr(cur, "parent"):
        parent = cur.parent
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or):
            # parent.values[0] must be settings.mediamtx_external_<x>_url
            if (parent.values and
                is_settings_attr(parent.values[0], expected_external)):
                return True
            # else: it's in an `or` chain but not paired correctly
            return False
        # Stop if we hit a statement-level node — beyond that, the
        # fallback chain is no longer "local" to this expression.
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.Module, ast.ClassDef)):
            return False
        cur = parent
    return False

def func_containing(tree, node):
    """Find the FunctionDef ancestor of `node`, or None."""
    cur = node
    while hasattr(cur, "parent"):
        if isinstance(cur.parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.parent
        cur = cur.parent
    return None

violations = []
for rel in ("server/routers/streams.py", "server/routers/recordings.py"):
    fn = REPO / rel
    if not fn.exists():
        violations.append(f"{rel}:?: source file missing")
        continue
    source_lines = fn.read_text().splitlines()
    tree = ast.parse("\n".join(source_lines), filename=str(fn))
    set_parents(tree)
    for node in ast.walk(tree):
        if not is_settings_internal_url(node):
            continue
        # Per-line pragma escape hatch — the source line itself
        # opts out of the check. This is the only allowlist
        # mechanism: explicit, local, reviewable in the diff.
        line_idx = node.lineno - 1
        if 0 <= line_idx < len(source_lines):
            if PRAGMA in source_lines[line_idx]:
                continue
        if in_fallback_chain(node):
            continue
        func = func_containing(tree, node)
        func_name = func.name if func else "<module>"
        violations.append(
            f"{rel}:{node.lineno}:{func_name}: "
            f"settings.{node.attr} not in fallback chain with "
            f"settings.{PAIRS[node.attr]}"
        )

for v in violations:
    print(v)
sys.exit(len(violations))
PY
}

# ── 1. positive contract: no bare internal-URL uses outside the allowlist ──
start_test "every settings.mediamtx_<x>_url in streams.py/recordings.py uses the fallback chain"
ast_out=$(run_ast_check)
ast_exit=$?
if [ "$ast_exit" -eq 0 ]; then
    pass
else
    fail "found ${ast_exit} bare internal-URL uses (browser would get unreachable URLs):
${ast_out}

Each violation should either:
  (1) be wrapped in
        settings.mediamtx_external_<x>_url or settings.mediamtx_<x>_url
      (the fallback chain — preferred for browser-facing fields), OR
  (2) carry a per-line pragma comment if the URL is used SERVER-
      SIDE only (e.g. a backend→mediamtx admin API call that never
      returns the URL to the browser):
        url = f\"{settings.mediamtx_playback_url}/list?path={path}\"  # url-internal-ok: server-side LIST call to mediamtx admin API
      The colon-separated rationale is required and reviewed."
fi

# ── 2. negative contract: every pragma comment has a rationale ──
# A bare ``# url-internal-ok`` is a code smell — the next reviewer
# has no way to tell whether it's legitimate. Require a colon-
# separated rationale: ``# url-internal-ok: <why>``. Cheap, makes
# the contract self-documenting.
start_test "every # url-internal-ok pragma carries a colon-separated rationale"
bare_pragmas=$(python3 - "$REPO_ROOT" <<'PY'
import re, sys
from pathlib import Path

REPO = Path(sys.argv[1])
PRAGMA = "url-internal-ok"
# Match the pragma when it's NOT immediately followed by ``:`` and
# something. ``url-internal-ok:`` is the only acceptable form;
# ``url-internal-ok`` alone (or followed by space/end of line)
# triggers a fail.
bad_re = re.compile(rf"{re.escape(PRAGMA)}(?![:]\s*\S)")

bad = []
for rel in ("server/routers/streams.py", "server/routers/recordings.py",
            "tests/host-hardening/test_url_fallback_chain.sh"):
    fn = REPO / rel
    if not fn.exists():
        continue
    for lineno, line in enumerate(fn.read_text().splitlines(), 1):
        # Skip the test's own definition and prose comments about
        # the pragma — we look at uses, not the spec.
        if rel.endswith(".sh"):
            continue
        if PRAGMA in line and bad_re.search(line):
            bad.append(f"{rel}:{lineno}: {line.strip()}")

for b in bad:
    print(b)
PY
)
if [ -z "$bare_pragmas" ]; then
    pass
else
    fail "bare # url-internal-ok pragmas without a rationale:
${bare_pragmas}
Add a colon-separated reason, e.g.
    # url-internal-ok: server-side LIST call to mediamtx admin API"
fi

# ── 3. structural: both files use the canonical fallback shape ──
# The fallback chain pattern is `external or internal or "http://..."`.
# Specifically test that the 3-element shape (external, internal, default)
# is what's used — guards against partial fixes like `external or internal`
# (no hardcoded default) that would crash when both settings are None
# during startup.
start_test "fallback chains include the hardcoded http://127.0.0.1:* default"
incomplete=$(python3 - "$REPO_ROOT" <<'PY'
import ast, sys
from pathlib import Path

REPO = Path(sys.argv[1])
PAIRS_EXT_TO_INT = {
    "mediamtx_external_base_url":     "mediamtx_base_url",
    "mediamtx_external_hls_url":      "mediamtx_hls_url",
    "mediamtx_external_rtsps_url":    "mediamtx_rtsps_url",
    "mediamtx_external_playback_url": "mediamtx_playback_url",
}

def is_settings_attr(node, attr_name):
    return (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and node.attr == attr_name)

incomplete = []
for rel in ("server/routers/streams.py", "server/routers/recordings.py"):
    fn = REPO / rel
    if not fn.exists():
        continue
    tree = ast.parse(fn.read_text(), filename=str(fn))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        if len(node.values) < 2:
            continue
        first = node.values[0]
        if not (isinstance(first, ast.Attribute) and
                isinstance(first.value, ast.Name) and
                first.value.id == "settings" and
                first.attr in PAIRS_EXT_TO_INT):
            continue
        # First operand is settings.mediamtx_external_*_url. Second
        # should be the internal counterpart. Third (if present) should
        # be a hardcoded http://... string literal.
        if len(node.values) < 3:
            incomplete.append(
                f"{rel}:{node.lineno}: fallback chain only has 2 operands; "
                f"missing hardcoded default — service would 500 if both "
                f"settings are None during startup"
            )
            continue
        third = node.values[2]
        # Acceptable schemes: http, https, rtsp, rtsps. The default
        # MUST be a string literal (not a settings reference or env
        # lookup) so the service can't 500 during startup.
        ok = (isinstance(third, ast.Constant) and
              isinstance(third.value, str) and
              any(third.value.startswith(s) for s in
                  ("http://", "https://", "rtsp://", "rtsps://")))
        if not ok:
            incomplete.append(
                f"{rel}:{node.lineno}: third operand is not a hardcoded "
                f"http(s)/rtsp(s) string literal"
            )

for v in incomplete:
    print(v)
PY
)
if [ -z "$incomplete" ]; then
    pass
else
    fail "fallback chains missing the hardcoded default:
${incomplete}"
fi

# ── Summary ────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo "Tests run    : ${TESTS_RUN}"
echo "Tests failed : ${TESTS_FAILED}"
if [ "$TESTS_FAILED" -eq 0 ]; then echo "Result       : all green"; exit 0
else echo "Result       : failures"; exit 1; fi
