#!/usr/bin/env bash
# ============================================================
# OpenNVR Camera Agent — one-command quickstart
# ============================================================
# Clone → run → talk to your cameras. Defaults to the LITE
# (Spotter) edition: text chat, detection only, ~1-2 GB RAM,
# no GPU. Heavier editions are one flag away.
#
# Usage (run from the repo root):
#   examples/camera-agent/quickstart.sh            # Spotter (lite, text)
#   examples/camera-agent/quickstart.sh --standard # Watch  (+ scene description)
#   examples/camera-agent/quickstart.sh --voice    # Sentinel (full hands-free voice)
#   examples/camera-agent/quickstart.sh --down      # stop the agent
#
# See examples/camera-agent/EDITIONS_AND_MODELS.md for what each edition does.
# ============================================================
set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
say() { printf "${CYAN}▸${NC} %s\n" "$1"; }
ok()  { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn(){ printf "${YELLOW}!${NC} %s\n" "$1"; }

# Resolve the repo root from this script's location so it works from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

PROFILE="camera-agent-lite"; EDITION="Spotter (lite · text · ~1-2 GB)"
ACTION="up"
for arg in "$@"; do
  case "$arg" in
    --standard) PROFILE="camera-agent-standard"; EDITION="Watch (standard · +scene description · ~3-4 GB)";;
    --voice|--full) PROFILE="camera-agent"; EDITION="Sentinel (full · hands-free voice · ~6-12 GB)";;
    --demo) PROFILE="camera-agent-demo"; EDITION="Demo (no camera · scripted scenes · instant)";;
    --lite|--spotter) PROFILE="camera-agent-lite";;
    --down|--stop) ACTION="down";;
    -h|--help)
      cat <<'EOF'
OpenNVR Camera Agent — one-command quickstart (run from repo root)

  examples/camera-agent/quickstart.sh            Spotter (lite · text · ~1-2 GB)
  examples/camera-agent/quickstart.sh --demo     Demo: no camera, scripted scenes (instant try / GIF)
  examples/camera-agent/quickstart.sh --standard Watch  (+ scene description · ~3-4 GB)
  examples/camera-agent/quickstart.sh --voice    Sentinel (full hands-free voice)
  examples/camera-agent/quickstart.sh --down     stop the agent

See examples/camera-agent/EDITIONS_AND_MODELS.md for what each edition does.
EOF
      exit 0;;
    *) warn "ignoring unknown arg: $arg";;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  warn "Docker is required but was not found on PATH. Install Docker Desktop / Engine first."
  exit 1
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.camera-agent.yml)

if [ "$ACTION" = "down" ]; then
  say "Stopping the camera agent…"
  "${COMPOSE[@]}" --profile camera-agent-lite --profile camera-agent-standard --profile camera-agent-demo --profile camera-agent down
  ok "Stopped."
  exit 0
fi

# 1) Ensure a .env exists (secrets). Prefer the repo's generator; fall back to
#    the example file so a fresh clone still comes up for a local demo.
if [ ! -f .env ]; then
  if [ -x ./scripts/generate-secrets.sh ]; then
    say "No .env found — generating fresh secrets…"
    ./scripts/generate-secrets.sh --write
  else
    say "No .env found — seeding from .env.example (dev secrets; change before exposing)…"
    cp .env.example .env
  fi
  ok ".env ready."
else
  ok ".env already present."
fi

# 2) Bring up the chosen edition. The lite/standard profiles auto-pull the
#    small LLM and start only the services that edition needs.
say "Starting edition: ${EDITION}"
say "Profile: ${PROFILE} (Ctrl-C is safe; containers run detached)"
"${COMPOSE[@]}" --profile "$PROFILE" up -d

echo
ok "Camera agent is starting."
printf "  Open ${GREEN}http://localhost:9100/demo${NC} and "
if [ "$PROFILE" = "camera-agent" ]; then
  printf "click Start, then speak.\n"
else
  printf "TYPE a question, e.g. \"how many people are at the door?\"\n"
fi
echo
say "First boot pulls the model and warms up — give it a minute."
say "Logs:  ${COMPOSE[*]} --profile ${PROFILE} logs -f"
say "Stop:  examples/camera-agent/quickstart.sh --down"
