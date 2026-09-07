#!/usr/bin/env bash
# llm-optimizer v2 — Linux installer. Keep optimizer_support.py beside this file.
# Uses official agent integrations; never installs permission-changing hooks.
set -uo pipefail

VERSION=2.1.0
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SUPPORT="$SCRIPT_DIR/optimizer_support.py"
DRY_RUN=0 YES=0 SKIP_RTK=0 SKIP_MEMORY=0 INSTALL_DEPS=0 LINGER=0
AGENTS='' REPORT='' SERVER="${AI_MEMORY_SERVER_URL:-}" SERVICE=auto
DATA="${AI_MEMORY_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-memory}"
CONFIG='' STRATEGY='' ROUTING='' AIM_VERSION='' RTK_VERSION=''
BIN_DIR="$HOME/.local/bin"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/llm-optimizer"
RELEASES="${XDG_DATA_HOME:-$HOME/.local/share}/llm-optimizer/releases"
ERRORS=0 RUN_DIR='' REPORT_FD='' LOCK_FD=''
SELECTED=() RESULTS=() TARGETS=()

usage() {
    cat <<'HELP'
llm-optimizer.sh v2 — Linux, Bash 4+, Python 3.8+
Keep optimizer_support.py alongside this script.
  --yes, -y                    Configure all detected supported agents
  --agents claude,codex,agy     Select installed agents (or all)
  --dry-run                    Plan only; writes the debug log, no installation
  --report PATH                Create a NEW private report, including in dry-run
  --skip-rtk | --skip-ai-memory Skip a component; skipping both is a no-op
  --install-deps               Install missing curl using a supported package manager
  --server-url URL             Reuse an existing server; never create a service
  --data-dir PATH              Local ai-memory data directory
  --config PATH                Default: DATA_DIR/config.toml
  --service auto|user|none      Local systemd user service; none requires a running server
  --enable-linger              Opt in to startup without login for a managed service
  --project-strategy basename|repo-root
                               Omission preserves the upstream install policy
  --routing-target PATH        Project instruction target; otherwise AGENTS.md/CLAUDE.md
  --ai-memory-version TAG      Explicit release; otherwise reuse an existing executable
  --rtk-version TAG            Explicit release; otherwise reuse an existing executable
  --help, -h
Run as the account that launches the agents. Shell startup files are not edited.
Official Codex/Antigravity RTK integration uses instructions, not custom hooks.
RTK setup also installs compact command guidance in agent instruction files.
Every invocation appends live diagnostics to ./llm-optimizer.log (private file).
HELP
}
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
bad_arg() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
log() {
    printf '%s\n' "$*"
    [ -z "$REPORT_FD" ] || printf '%s\n' "$*" >&"$REPORT_FD" || fail 'Report write failed'
}
error() { ERRORS=$((ERRORS + 1)); log "ERROR: $*"; }
py() { python3 "$SUPPORT" "$@" 2>&"${LLM_OPTIMIZER_DETAIL_FD:-2}"; }
cleanup() { [ -z "$RUN_DIR" ] || rm -rf -- "$RUN_DIR"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

parse_args() {
    local opt value
    while [ "$#" -gt 0 ]; do
        opt=$1
        case "$opt" in
            --help|-h) usage; exit 0 ;;
            --yes|-y) YES=1; shift; continue ;;
            --dry-run) DRY_RUN=1; shift; continue ;;
            --skip-rtk) SKIP_RTK=1; shift; continue ;;
            --skip-ai-memory) SKIP_MEMORY=1; shift; continue ;;
            --install-deps) INSTALL_DEPS=1; shift; continue ;;
            --enable-linger) LINGER=1; shift; continue ;;
            --agents|--report|--server-url|--data-dir|--config|--service|--project-strategy|--routing-target|--ai-memory-version|--rtk-version)
                [ "$#" -ge 2 ] && [ -n "$2" ] && [[ "$2" != -* ]] || bad_arg "$opt needs a value"
                value=$2; shift 2 ;;
            --agents=*|--report=*|--server-url=*|--data-dir=*|--config=*|--service=*|--project-strategy=*|--routing-target=*|--ai-memory-version=*|--rtk-version=*)
                value=${opt#*=}; opt=${opt%%=*}; shift
                [ -n "$value" ] || bad_arg "$opt needs a value" ;;
            *) bad_arg "Unknown option: $opt" ;;
        esac
        case "$opt" in
            --agents) AGENTS=$value ;; --report) REPORT=$value ;;
            --server-url) SERVER=$value ;; --data-dir) DATA=$value ;;
            --config) CONFIG=$value ;; --service) SERVICE=$value ;;
            --project-strategy) STRATEGY=$value ;; --routing-target) ROUTING=$value ;;
            --ai-memory-version) AIM_VERSION=$value ;; --rtk-version) RTK_VERSION=$value ;;
        esac
    done
    case "$SERVICE" in auto|user|none) ;; *) bad_arg 'Invalid service mode' ;; esac
    case "$STRATEGY" in ''|basename|repo-root) ;; *) bad_arg 'Invalid project strategy' ;; esac
}

select_agents() {
    local agent bin item requested=${AGENTS:-all} reply
    local -a names
    IFS=',' read -r -a names <<< "$requested"
    [[ "$requested" != ,* && "$requested" != *, && "$requested" != *,,* ]] || bad_arg 'Empty agent name'
    for item in "${names[@]}"; do
        case "$item" in claude|claude-code|codex|codex-cli|agy|antigravity|all|todos) ;; *) bad_arg "Unknown agent: $item" ;; esac
    done
    for agent in claude codex antigravity; do
        case ",$requested," in
            *,all,*|*,todos,*) ;;
            *) case "$agent" in
                claude) [[ ",$requested," == *,claude,* || ",$requested," == *,claude-code,* ]] || continue ;;
                codex) [[ ",$requested," == *,codex,* || ",$requested," == *,codex-cli,* ]] || continue ;;
                antigravity) [[ ",$requested," == *,agy,* || ",$requested," == *,antigravity,* ]] || continue ;;
            esac ;;
        esac
        bin=$agent; [ "$agent" != antigravity ] || bin=agy
        if ! command -v "$bin" >/dev/null; then
            case "$requested" in all|todos) continue ;; *) bad_arg "Selected agent not executable: $bin" ;; esac
        fi
        if [ -z "$AGENTS" ] && [ "$YES" = 0 ] && [ -t 0 ] && [ "$DRY_RUN" = 0 ]; then
            read -r -p "Configure $agent? [Y/n] " reply || fail 'Selection interrupted'
            case "$reply" in n|N|no) continue ;; esac
        fi
        SELECTED+=("$agent")
    done
}

run() {
    local description=$1 rc started=$SECONDS; shift
    log "RUN: $description"
    # Hidden prompts must receive EOF, never consume the operator's terminal.
    # Preserve upstream defaults/consent; do not auto-answer yes.
    # Force termination if a child ignores the initial timeout signal.
    timeout --kill-after=5 180 "$@" </dev/null >&"${LLM_OPTIMIZER_DETAIL_FD:-1}" 2>&1
    rc=$?
    log "DONE: $description (status $rc, $((SECONDS - started))s)"
    if [ "$rc" -ne 0 ]; then
        error "$description failed (status $rc). Dependent phase blocked; see llm-optimizer.log for diagnostics."
        return 1
    fi
    return 0
}

ensure_curl() {
    command -v curl >/dev/null && return 0
    [ "$INSTALL_DEPS" = 1 ] || { error 'curl missing; install it or pass --install-deps'; return 1; }
    local -a elevate=() install=()
    if [ "$(id -u)" -ne 0 ]; then
        if command -v sudo >/dev/null; then elevate=(sudo)
        elif command -v doas >/dev/null; then elevate=(doas)
        else error 'No sudo/doas for package installation'; return 1; fi
    fi
    if command -v apt-get >/dev/null; then
        run 'Refresh apt for missing dependency' "${elevate[@]}" apt-get update -qq || return 1
        install=(apt-get install -y -qq curl)
    elif command -v dnf >/dev/null; then install=(dnf install -y curl)
    elif command -v pacman >/dev/null; then install=(pacman -S --noconfirm --needed curl)
    elif command -v zypper >/dev/null; then install=(zypper --non-interactive install curl)
    elif command -v apk >/dev/null; then install=(apk add --no-cache curl)
    else error 'Unsupported package manager'; return 1; fi
    run 'Install curl' "${elevate[@]}" "${install[@]}" && command -v curl >/dev/null
}
fetch() {
    run "Download $1" curl --proto '=https' --proto-redir '=https' -fsSL --connect-timeout 10 \
        --max-time 180 --retry 2 --retry-max-time 240 "$1" -o "$2"
}

install_release() {
    local name=$1 repo=$2 suffix=$3 tag=$4 base asset checksum stage relative final cap
    local -a capabilities
    [ -n "$suffix" ] || { error "No supported release for $ARCH"; return 1; }
    if { [ "$name" = ai-memory ] || [[ "$suffix" == *linux-gnu ]]; } && ! getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
        error "$name release needs glibc; install a compatible native build first"; return 1
    fi
    ensure_curl || return 1
    if [ -z "$tag" ]; then
        fetch "https://api.github.com/repos/$repo/releases/latest" "$RUN_DIR/release.json" || { error "Cannot resolve $name release"; return 1; }
        tag=$(py release-tag "$RUN_DIR/release.json") || return 1
    fi
    py tag "$tag" >/dev/null || return 1
    asset="$name-$suffix.tar.gz"; base="https://github.com/$repo/releases/download/$tag"
    checksum=checksums.txt; [ "$name" != ai-memory ] || checksum="$asset.sha256"
    fetch "$base/$asset" "$RUN_DIR/$asset" && fetch "$base/$checksum" "$RUN_DIR/checksum" || { error "Cannot download $name artifact/checksum"; return 1; }
    mkdir -p "$RELEASES" || return 1
    stage=$(mktemp -d "$RELEASES/.stage.XXXXXXXX") || return 1
    relative=$(py extract "$RUN_DIR/$asset" "$RUN_DIR/checksum" "$asset" "$stage" "$name") || { rm -rf "$stage"; error "$name verification failed"; return 1; }
    run "Validate staged $name" "$stage/$relative" --version || { rm -rf "$stage"; return 1; }
    if [ "$name" = ai-memory ]; then capabilities=(status install-mcp install-hooks install-instructions)
    else capabilities=(init rewrite proxy); fi
    for cap in "${capabilities[@]}"; do
        run "Validate staged $name capability: $cap" "$stage/$relative" "$cap" --help || { rm -rf "$stage"; return 1; }
    done
    final="$RELEASES/$name-$tag"
    if [ -L "$final" ]; then rm -rf "$stage"; error 'Release destination is a symlink; preserved'; return 1; fi
    if [ -e "$final" ]; then
        if ! diff -qr "$stage" "$final" >/dev/null 2>&1; then rm -rf "$stage"; error 'Existing release differs; preserved'; return 1; fi
        rm -rf "$stage"
    else mv "$stage" "$final" || return 1; fi
    py publish "$final/$relative" "$BIN_DIR/$name" "$RELEASES" || return 1
    export PATH="$BIN_DIR:$PATH"
    hash -r
    if [ "$name" = ai-memory ]; then AIM_BIN="$final/$relative"; else RTK_BIN="$final/$relative"; fi
    log "Installed $name $tag (full release retained): $final/$relative"
    log "Agent launch environments must put $BIN_DIR first on PATH; restart existing agents after updating their environment."
}

prepare_server() {
    log 'STEP: Discover and prepare ai-memory server'
    export AI_MEMORY_SERVER_URL="$SERVER"
    if [ "$REUSE" = 1 ] || [ "$SERVICE" = none ]; then
        run 'Identify/authenticate reused ai-memory server' "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" status || return 1
        log 'Server reused; persistence managed externally.'; return 0
    fi
    py native "$AIM_BIN" || { error 'Local service needs native ELF, not a Docker wrapper'; return 1; }
    command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1 || { error 'No systemd user bus; start a server explicitly and use --service none or --server-url'; return 1; }
    local unit="$HOME/.config/systemd/user/ai-memory.service" outcome attempt fragment
    fragment=$(systemctl --user show ai-memory.service --property=FragmentPath --value 2>/dev/null) || fragment=''
    if [ ! -e "$unit" ] && [ ! -L "$unit" ] && [ -n "$fragment" ]; then
        run 'Validate server before preserving external service definition' "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" status || return 1
        log 'External service definition preserved; no overriding user unit created.'
        return 0
    fi
    if [ -e "$unit" ] || [ -L "$unit" ]; then
        if ! head -1 "$unit" | grep -qx '# Managed by llm-optimizer v2'; then
            run 'Validate server before preserving unmanaged unit' "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" status || return 1
            log 'Unmanaged unit preserved. Its config and boot persistence were not changed or validated.'
            return 0
        fi
    elif { ensure_curl || return 1; } && curl -s --connect-timeout 1 --max-time 2 -o /dev/null "$SERVER/mcp"; then
        run 'Identify existing listener' "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" status || return 1
        log 'Existing listener reused; no competing service created.'; return 0
    fi
    # Do not silently start a daemon with a different auth/provider environment.
    if env | cut -d= -f1 | grep -qE '^(AI_MEMORY_LLM_|AI_MEMORY_EMBEDDING_|AI_MEMORY_AUTH_TOKEN$|ANTHROPIC_API_KEY$|OPENAI_API_KEY$|LLM_API_KEY$)'; then
        error 'Persist provider/auth environment in the server configuration before creating a local service'; return 1
    fi
    run 'Initialize explicit ai-memory config/data' "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" init || return 1
    outcome=$(py unit "$unit" "$AIM_BIN" "$DATA" "$CONFIG") || { error 'Service configuration failed'; return 1; }
    run 'Reload systemd user units' systemctl --user daemon-reload || return 1
    run 'Enable ai-memory user service' systemctl --user enable ai-memory.service || return 1
    if [ "$outcome" = changed ]; then run 'Restart changed ai-memory service' systemctl --user restart ai-memory.service || return 1
    else run 'Ensure ai-memory service started' systemctl --user start ai-memory.service || return 1; fi
    if [ "$LINGER" = 1 ]; then run 'Enable requested linger' loginctl enable-linger "$(id -un)" || return 1; fi
    for attempt in {1..20}; do
        log "STEP: ai-memory health check $attempt/20"
        if timeout --kill-after=1 3 "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" status </dev/null >&"${LLM_OPTIMIZER_DETAIL_FD:-1}" 2>&1; then
            log 'DONE: ai-memory health check (status 0)'
            return 0
        fi
        sleep 1
    done
    error 'ai-memory did not become healthy; agent configuration blocked'; return 1
}

configure_memory() {
    local agent=$1 upstream=$1
    local -a strategy=()
    case "$agent" in claude) upstream=claude-code ;; antigravity) upstream=antigravity-cli ;; esac
    [ -z "$STRATEGY" ] || strategy=(--project-strategy "$STRATEGY")
    run "ai-memory MCP: $agent" "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" install-mcp --client "$upstream" --server-url "$SERVER/mcp" --apply || return 1
    run "ai-memory hooks: $agent" "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" install-hooks --agent "$upstream" --server-url "$SERVER" "${strategy[@]}" --apply || return 1
    RESULTS+=("ai-memory/$agent: configuration applied; live capture pending verification")
}

configure_rtk() {
    local agent=$1 instruction_target
    # Exact legacy registrations only; preserve symlinks and unrelated hooks.
    case "$agent" in
        codex) py legacy "$CODEX_ROOT/hooks.json" codex "$BIN_DIR/rtk-llm-hook.py" || return 1 ;;
        antigravity) py legacy "$AGY_ROOT/hooks.json" agy "$BIN_DIR/rtk-llm-hook.py" || return 1 ;;
    esac
    case "$agent" in
        claude)
            mkdir -p "$CLAUDE_ROOT" || { error 'Cannot create Claude configuration directory'; return 1; }
            run 'RTK official Claude integration' "$RTK_BIN" init -g --auto-patch || return 1 ;;
        codex) run 'RTK official Codex instructions' "$RTK_BIN" init -g --codex || return 1 ;;
        antigravity) run 'RTK official Antigravity project instructions' "$RTK_BIN" init --agent antigravity || return 1 ;;
    esac
    case "$agent" in
        claude) instruction_target="$CLAUDE_ROOT/CLAUDE.md" ;;
        codex) instruction_target="$CODEX_ROOT/AGENTS.md" ;;
        antigravity) instruction_target="$PWD/AGENTS.md" ;;
    esac
    run "RTK efficient command guidance: $agent" python3 "$SUPPORT" rtk-instructions "$instruction_target" || return 1
    RESULTS+=("rtk/$agent: official integration applied; agent loading pending verification")
}

main() {
    log 'STEP: Validate arguments and prerequisites'
    parse_args "$@"
    if [ "$SKIP_RTK" = 1 ] && [ "$SKIP_MEMORY" = 1 ]; then printf 'Both skipped; no changes.\n'; return 0; fi
    [ "${BASH_VERSINFO[0]}" -ge 4 ] || fail 'Bash 4+ required'
    [ "$(uname -s)" = Linux ] || fail 'v2 supports Linux only'
    command -v python3 >/dev/null && [ -f "$SUPPORT" ] || fail 'Python 3.8+ and sibling optimizer_support.py required'
    python3 -c 'import sys; sys.exit(sys.version_info < (3,8))' || fail 'Python 3.8+ required'
    CONFIG=${CONFIG:-$DATA/config.toml}
    DATA=$(py path "$DATA") && CONFIG=$(py path "$CONFIG") || return 1
    REUSE=0
    if [ "$SKIP_MEMORY" = 1 ]; then
        SERVER=''
    elif [ -n "$SERVER" ]; then
        REUSE=1; SERVER=$(py url "$SERVER") || return 1
        [ "$LINGER" = 0 ] || bad_arg '--enable-linger cannot be combined with server reuse'
    else SERVER=http://127.0.0.1:49374; fi
    [ "$SERVICE" != none ] || [ "$LINGER" = 0 ] || bad_arg 'Linger requires a managed service'
    local tag agent cap ready target
    for tag in "$AIM_VERSION" "$RTK_VERSION"; do [ -z "$tag" ] || py tag "$tag" >/dev/null || return 1; done
    CLAUDE_ROOT=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
    CODEX_ROOT=${CODEX_HOME:-$HOME/.codex}
    AGY_ROOT="$HOME/.gemini/config"
    log 'STEP: Discover and select agents'
    select_agents
    log 'STEP: Validate existing agent configuration'
    for agent in "${SELECTED[@]}"; do
        case "$agent" in
            claude) py json-check "$CLAUDE_ROOT/settings.json" || return 1 ;;
            codex) py json-check "$CODEX_ROOT/hooks.json" || return 1 ;;
            antigravity) py json-check "$AGY_ROOT/hooks.json" || return 1 ;;
        esac
    done
    if [ -n "$REPORT" ]; then
        # Exclusive open, with the same FD used throughout; never truncate a destination.
        set -C
        if ! { umask 077; exec {REPORT_FD}>"$REPORT"; } 2>/dev/null; then set +C; fail 'Report must be a NEW writable file'; fi
        set +C
    fi
    log "# llm-optimizer v$VERSION"
    log "Agents: ${SELECTED[*]:-none (binaries only)}"
    if [ "$SKIP_MEMORY" = 0 ]; then
        log "ai-memory: $SERVER; config=$CONFIG; data=$DATA"
        log "Hook strategy: ${STRATEGY:-upstream policy (new installs default to basename)}"
    else log 'ai-memory: skipped'; fi
    log 'RTK uses official integrations; no custom rewrite hook or permission decisions.'
    AIM_BIN=$(command -v ai-memory 2>/dev/null || true)
    RTK_BIN=$(command -v rtk 2>/dev/null || true)
    [ -z "$AIM_BIN" ] || AIM_BIN=$(py path "$AIM_BIN") || return 1
    [ -z "$RTK_BIN" ] || RTK_BIN=$(py path "$RTK_BIN") || return 1
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64|amd64) AIM_ASSET=linux-x86_64; RTK_ASSET=x86_64-unknown-linux-musl ;;
        aarch64|arm64) AIM_ASSET=linux-aarch64; RTK_ASSET=aarch64-unknown-linux-gnu ;;
        *) AIM_ASSET=''; RTK_ASSET='' ;;
    esac
    if [ "$DRY_RUN" = 1 ]; then
        [ "$SKIP_MEMORY" = 1 ] || log "PLAN ai-memory: ${AIM_BIN:-verified release download} ${AIM_VERSION:+target=$AIM_VERSION}; service=$SERVICE; reuse=$REUSE"
        [ "$SKIP_RTK" = 1 ] || log "PLAN RTK: ${RTK_BIN:-verified release download} ${RTK_VERSION:+target=$RTK_VERSION}"
        log 'PLAN: validate capabilities and conflicts; configure selected MCP/hooks/instructions and project routing; remove exact v1 custom RTK registrations.'
        [ "$SKIP_RTK" = 1 ] || log 'PLAN: add compact RTK command guidance to selected agent instruction files.'
        log 'Only the debug log and any requested report are written. No analytics, downloads, agent commands or services executed.'
        return 0
    fi
    command -v flock >/dev/null || fail 'flock required (util-linux)'
    command -v timeout >/dev/null || fail 'timeout required (coreutils)'
    log 'STEP: Acquire installation lock and prepare recovery snapshot'
    umask 077
    mkdir -p "$STATE" || return 1
    exec {LOCK_FD}>"$STATE/install.lock" || return 1
    flock -n "$LOCK_FD" || fail 'Another optimizer install is running'
    RUN_DIR=$(mktemp -d "$STATE/run.XXXXXXXX") || return 1
    # Record recoverable snapshots before official installers touch selected config.
    if [ "$SKIP_MEMORY" = 0 ] && [ "${#SELECTED[@]}" -gt 0 ]; then
        if [ -n "$ROUTING" ]; then TARGETS=("$ROUTING")
        else
            [[ " ${SELECTED[*]} " != *' claude '* ]] || TARGETS+=("$PWD/CLAUDE.md")
            if [[ " ${SELECTED[*]} " == *' codex '* || " ${SELECTED[*]} " == *' antigravity '* ]]; then TARGETS+=("$PWD/AGENTS.md"); fi
        fi
    fi
    local -a snapshot_targets=("${TARGETS[@]}")
    if [ "$SKIP_RTK" = 0 ] && [[ " ${SELECTED[*]} " == *' antigravity '* ]]; then
        snapshot_targets+=("$PWD/.agents/rules/antigravity-rtk-rules.md" "$PWD/AGENTS.md")
    fi
    local snapshot_result
    snapshot_result=$(py snapshot "$STATE" "${SELECTED[@]}" -- "${snapshot_targets[@]}") || return 1
    [ -z "$snapshot_result" ] || log "$snapshot_result"
    if [ "$SKIP_MEMORY" = 0 ]; then
        ready=1
        if [ -z "$AIM_BIN" ] || [ -n "$AIM_VERSION" ]; then install_release ai-memory akitaonrails/ai-memory "$AIM_ASSET" "$AIM_VERSION" || ready=0; fi
        if [ "$ready" = 1 ]; then
            for cap in status install-mcp install-hooks install-instructions; do run "ai-memory capability: $cap" "$AIM_BIN" "$cap" --help || ready=0; done
        fi
        if [ "$ready" = 1 ] && prepare_server; then
            for agent in "${SELECTED[@]}"; do configure_memory "$agent" || ready=0; done
            if [ "$ready" = 1 ] && [ "${#SELECTED[@]}" -gt 0 ]; then
                for target in "${TARGETS[@]}"; do
                    run 'Install ai-memory project routing/skills' "$AIM_BIN" --data-dir "$DATA" --config "$CONFIG" install-instructions --target "$target" || ready=0
                done
            fi
        else error 'ai-memory preparation failed; integrations blocked'; fi
    fi
    if [ "$SKIP_RTK" = 0 ]; then
        ready=1
        if [ -z "$RTK_BIN" ] || [ -n "$RTK_VERSION" ]; then install_release rtk rtk-ai/rtk "$RTK_ASSET" "$RTK_VERSION" || ready=0; fi
        if [ "$ready" = 1 ]; then
            for cap in init rewrite proxy; do run "RTK capability: $cap" "$RTK_BIN" "$cap" --help || ready=0; done
        fi
        if [ "$ready" = 1 ]; then
            for agent in "${SELECTED[@]}"; do configure_rtk "$agent" || error "RTK configuration failed: $agent"; done
        else error 'RTK preparation failed'; fi
    fi
    log ''
    for target in "${RESULTS[@]}"; do log "$target"; done
    log "Errors: $ERRORS"
    log "Executables: ai-memory=${AIM_BIN:-absent}; RTK=${RTK_BIN:-absent}"
    log 'Restart agents. Verify MCP memory_status and capture in a disposable session.'
    log 'Codex/Antigravity: explicitly finalize completed sessions when no true SessionEnd is available.'
    log 'Use rtk proxy for exact raw output. Analytics report estimates, not billed savings.'
    log 'Official installers manage integration trust and consent. Shell startup files were not edited by this installer.'
    [ "$ERRORS" -eq 0 ]
}

# Tests may source the functions; sourcing never executes installation.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    exec python3 "$SUPPORT" logged-run "$PWD/llm-optimizer.log" \
        bash -c 'source "$1"; shift; main "$@"' llm-optimizer "$SCRIPT_DIR/llm-optimizer.sh" "$@"
fi
