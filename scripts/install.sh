#!/usr/bin/env bash
# OpenNVR interactive installer for Linux and macOS.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BASE_COMPOSE="docker-compose.yml"
cd "$PROJECT_ROOT"

if [[ ! -t 0 ]]; then
    echo "This installer is interactive. Run it from a terminal: ./scripts/install.sh" >&2
    exit 1
fi

info() { printf '  %s\n' "$*"; }
ok() { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
die() { printf '  ✗ %s\n' "$*" >&2; exit 1; }
ask_yes_no() {
    local prompt="$1" default="${2:-n}" answer hint
    [[ "$default" == "y" ]] && hint="Y/n" || hint="y/N"
    read -r -p "  $prompt [$hint]: " answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}
ask_value() {
    local prompt="$1" default="$2" answer
    read -r -p "  $prompt [$default]: " answer
    REPLY="${answer:-$default}"
}
ask_secret() {
    local prompt="$1" answer
    read -r -s -p "  $prompt: " answer
    printf '\n'
    REPLY="$answer"
}

detect_platform() {
    case "$(uname -s)" in
        Linux*) PLATFORM="Linux"; DEFAULT_RECORDINGS="/var/lib/opennvr/recordings" ;;
        Darwin*) PLATFORM="macOS"; DEFAULT_RECORDINGS="/Users/Shared/opennvr-recordings" ;;
        *) die "Unsupported platform. On Windows run .\\scripts\\install.ps1" ;;
    esac
    ok "Detected $PLATFORM (Docker bridge mode)"
}

check_prerequisites() {
    command -v docker >/dev/null 2>&1 || die "Docker is not installed"
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
    docker info >/dev/null 2>&1 || die "Docker is not running"
    command -v openssl >/dev/null 2>&1 || die "openssl is required to generate credentials"
    [[ -f "$BASE_COMPOSE" ]] || die "$BASE_COMPOSE was not found in $PROJECT_ROOT"
}

env_get() {
    local key="$1" value
    value=$(grep -E "^${key}=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true)
    value=$(printf '%s' "$value" | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')
    printf '%s' "$value"
}
env_set() {
    local key="$1" value="$2" tmp found=false line
    tmp=$(mktemp "${PROJECT_ROOT}/.env.tmp.XXXXXX")
    if [[ -f .env ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$line" == "$key="* ]]; then
                if [[ "$found" == false ]]; then printf '%s=%s\n' "$key" "$value" >> "$tmp"; found=true; fi
            else
                printf '%s\n' "$line" >> "$tmp"
            fi
        done < .env
    fi
    [[ "$found" == true ]] || printf '\n%s=%s\n' "$key" "$value" >> "$tmp"
    mv "$tmp" .env
}
is_missing_or_placeholder() {
    local value="$1"
    [[ -z "$value" || "$value" =~ ^(dev_|insecure_|change_me|your_|changeme|placeholder|dummy|CKLghtP4rWz8J9vN2xQ5mT7yU8kF6bD3eH1aG4cS0wE=) ]]
}
random_hex() { openssl rand -hex "$1"; }
random_password() { openssl rand -hex 16; }
random_fernet() { openssl rand -base64 32 | tr '/+' '_-' | tr -d '\n'; }

ensure_plain_value() {
    local key="$1" label="$2" default="$3" current
    current=$(env_get "$key")
    [[ -n "$current" ]] && return 0
    ask_value "$label" "$default"
    env_set "$key" "$REPLY"
}
ensure_secret_value() {
    local key="$1" label="$2" generated="$3" current
    current=$(env_get "$key")
    if ! is_missing_or_placeholder "$current"; then
        ok "$label already configured"
        return 0
    fi
    if ask_yes_no "$label is missing or insecure. Use a newly generated value?" y; then
        env_set "$key" "$generated"
    else
        ask_secret "Enter $label"
        [[ -n "$REPLY" ]] || die "$label cannot be empty"
        env_set "$key" "$REPLY"
    fi
    ok "$label configured"
}

prepare_environment() {
    if [[ ! -f .env ]]; then
        [[ -f .env.example ]] || die ".env.example is missing"
        cp .env.example .env
        ok "Created .env from .env.example"
    else
        ok "Using existing .env; existing values will be preserved"
    fi

    ensure_plain_value POSTGRES_USER "PostgreSQL user" "opennvr_user"
    ensure_plain_value POSTGRES_DB "PostgreSQL database" "opennvr_db"
    ensure_plain_value RECORDINGS_PATH "Recordings path" "$DEFAULT_RECORDINGS"
    ensure_plain_value DEFAULT_ADMIN_USERNAME "Administrator username" "admin"
    ensure_plain_value DEFAULT_ADMIN_EMAIL "Administrator email" "admin@opennvr.local"
    ensure_secret_value POSTGRES_PASSWORD "PostgreSQL password" "$(random_password)"
    ensure_secret_value SECRET_KEY "JWT signing key" "$(random_hex 32)"
    ensure_secret_value CREDENTIAL_ENCRYPTION_KEY "credential encryption key" "$(random_fernet)"
    ensure_secret_value INTERNAL_API_KEY "internal API key" "$(random_password)"
    ensure_secret_value MEDIAMTX_SECRET "MediaMTX webhook secret" "$(random_hex 32)"
    mkdir -p "$(env_get RECORDINGS_PATH)" 2>/dev/null || warn "Could not create the recordings directory; Docker will try"
}

find_example_compose() {
    local name="$1" candidate
    for candidate in "docker-compose.${name}.yml" "docker-compose.${name}.yaml" \
                     "examples/${name}/docker-compose.yml" "examples/${name}/docker-compose.yaml" \
                     "examples/${name}/compose.yml" "examples/${name}/compose.yaml"; do
        [[ -f "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

prompt_overlay_defaults() {
    local file="$1" spec body key default current
    while IFS= read -r spec; do
        [[ -n "$spec" ]] || continue
        body="${spec#\$\{}"; body="${body%\}}"
        key="${body%%:-*}"; default="${body#*:-}"
        current=$(env_get "$key")
        [[ -n "$current" ]] && continue
        ask_value "$key" "$default"
        env_set "$key" "$REPLY"
    done < <(grep -oE '\$\{[A-Z][A-Z0-9_]*:-[^}]+\}' "$file" | sort -u || true)
}

choose_example() {
    EXAMPLE_NAME=""; EXAMPLE_COMPOSE=""; EXAMPLE_PROFILE=""
    env_set OPENNVR_EXAMPLE ""
    env_set OPENNVR_EXAMPLE_COMPOSE ""
    env_set OPENNVR_EXAMPLE_PROFILE ""

    ask_yes_no "Install an example AI stack?" n || return 0
    local names=() dir name manifest choice index
    while IFS= read -r dir; do names+=("$(basename "$dir")"); done < <(find examples -mindepth 1 -maxdepth 1 -type d | sort)
    [[ ${#names[@]} -gt 0 ]] || { warn "No examples were found"; return 0; }

    printf '\n  Available examples:\n'
    index=1
    for name in "${names[@]}"; do
        if manifest=$(find_example_compose "$name"); then
            printf '  %2d. %-30s [installable: %s]\n' "$index" "$name" "$manifest"
        else
            printf '  %2d. %-30s [no Compose manifest]\n' "$index" "$name"
        fi
        index=$((index + 1))
    done
    printf '   0. Core stack only\n\n'
    read -r -p "  Select an example [0]: " choice
    choice="${choice:-0}"
    [[ "$choice" =~ ^[0-9]+$ ]] || die "Invalid selection"
    (( choice == 0 )) && return 0
    (( choice >= 1 && choice <= ${#names[@]} )) || die "Selection out of range"

    name="${names[$((choice - 1))]}"
    manifest=$(find_example_compose "$name") || die "The '$name' example has no Docker Compose manifest"
    EXAMPLE_NAME="$name"; EXAMPLE_COMPOSE="$manifest"; EXAMPLE_PROFILE="$name"
    if [[ "$name" == "camera-agent" ]]; then
        ask_value "Camera agent mode: 1=voice, 2=chat" "1"
        [[ "$REPLY" == "2" ]] && EXAMPLE_PROFILE="camera-agent-chat" || EXAMPLE_PROFILE="camera-agent"
    fi
    prompt_overlay_defaults "$manifest"
    env_set OPENNVR_EXAMPLE "$EXAMPLE_NAME"
    env_set OPENNVR_EXAMPLE_COMPOSE "$EXAMPLE_COMPOSE"
    env_set OPENNVR_EXAMPLE_PROFILE "$EXAMPLE_PROFILE"
    ok "Selected $EXAMPLE_NAME ($EXAMPLE_PROFILE)"
}

pull_and_build() {
    info "Pulling the OpenNVR core stack..."
    docker compose -f "$BASE_COMPOSE" pull --ignore-buildable

    choose_example
    COMPOSE_ARGS=(-f "$BASE_COMPOSE")
    if [[ -n "$EXAMPLE_COMPOSE" ]]; then
        COMPOSE_ARGS+=(-f "$EXAMPLE_COMPOSE" --profile "$EXAMPLE_PROFILE")
        info "Pulling images for $EXAMPLE_NAME..."
        docker compose "${COMPOSE_ARGS[@]}" pull --ignore-buildable
    fi
    info "Building services that do not publish a pre-built image..."
    docker compose "${COMPOSE_ARGS[@]}" build
}

main() {
    printf '\nOpenNVR interactive installer\n\n'
    detect_platform
    check_prerequisites
    prepare_environment
    pull_and_build
    printf '\n  Configuration and images are ready. Starting OpenNVR...\n\n'
    exec "$PROJECT_ROOT/start.sh" up
}
main "$@"