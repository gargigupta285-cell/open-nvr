#!/usr/bin/env bash
# ============================================================
# Shared helpers for the host-hardening test suites
# (ISSUE-19).
#
# Source from each test with:
#   . "$(dirname "$0")/_lib.sh"
#
# What's here:
#   * ``require_python_yaml`` — exits with a clear install command
#     if pyyaml isn't importable. Tests that load
#     docker-compose*.yml with yaml.safe_load need this.
#   * ``require_bash_4`` — exits with a clear message if running
#     on bash 3.2 (macOS default). Use only for tests that
#     genuinely need bash 4+ features.
# ============================================================

# Halt with a copy-pasteable install command if pyyaml is missing.
# Reports the python3 binary it tried so operators don't have to guess
# which interpreter to install into.
require_python_yaml() {
    if ! python3 -c 'import yaml' >/dev/null 2>&1; then
        local py
        py=$(command -v python3 || echo "(python3 not on PATH)")
        echo "✗ This test needs PyYAML on the python3 it runs against." >&2
        echo "  python3 in PATH: ${py}" >&2
        echo "" >&2
        echo "  Install with one of:" >&2
        echo "    python3 -m pip install --user pyyaml" >&2
        echo "    pip3 install pyyaml" >&2
        echo "    # macOS with Homebrew:" >&2
        echo "    brew install python  &&  python3 -m pip install pyyaml" >&2
        echo "    # Debian / Ubuntu:" >&2
        echo "    sudo apt install python3-yaml" >&2
        echo "" >&2
        echo "  Then re-run this test." >&2
        exit 2
    fi
}

# Halt with a clear message if running on bash 3.2 (macOS default).
# Use sparingly — most tests should be written to bash 3.2
# compatibility so they run on every operator's machine without
# requiring ``brew install bash``. This helper is for tests where
# bash 4+ is genuinely needed.
require_bash_4() {
    if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
        echo "✗ This test needs bash 4+ (you have bash ${BASH_VERSION})." >&2
        echo "" >&2
        echo "  macOS ships bash 3.2 because Apple stopped updating after" >&2
        echo "  bash went GPLv3. Install a current bash with Homebrew:" >&2
        echo "    brew install bash" >&2
        echo "    bash $0" >&2
        echo "" >&2
        echo "  (Or open an issue — most tests should be 3.2 compatible.)" >&2
        exit 2
    fi
}
