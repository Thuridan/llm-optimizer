# llm-optimizer

**A Linux installer that connects [ai-memory](https://github.com/akitaonrails/ai-memory) and
[RTK](https://github.com/rtk-ai/rtk) to your coding agents**, so Claude Code, Codex, and
Antigravity get persistent cross-session memory and drastically smaller command output — without
hand-editing hooks, instruction files, or systemd units yourself.

- **[ai-memory](https://github.com/akitaonrails/ai-memory)** gives agents durable knowledge across
  sessions via MCP and lifecycle capture, so context survives restarts instead of resetting every
  conversation.
- **[RTK](https://github.com/rtk-ai/rtk)** compresses routine shell output (git, test runners,
  ripgrep, ...) so agents burn far fewer tokens on command noise. Measured with RTK v0.48.0: 40
  passing unittest cases dropped from 1,919 raw bytes to 127 filtered bytes — a 93.4% reduction —
  while exit status and failure diagnostics were preserved.

The installer only uses each tool's **official integration path** (MCP registration, agent hooks,
global/project instruction files) — it never installs custom permission-changing hooks.

## Requirements

- Linux, Bash 4+, Python 3.8+
- `flock` and `timeout`
- `curl`, for release downloads and the local service health probe (not required to reuse an
  existing remote memory server with already-installed binaries)

## Install

Keep `optimizer_support.py` next to `llm-optimizer.sh` — the installer depends on it. Run as the
account that launches your agents, from the project whose instructions you want to configure.

```bash
git clone https://github.com/Thuridan/llm-optimizer.git
cd llm-optimizer
./llm-optimizer.sh --help
```

## Usage

```bash
# See what would happen, without installing or downloading anything
./llm-optimizer.sh --dry-run --agents codex

# Configure Codex against a self-hosted ai-memory server
./llm-optimizer.sh --agents codex --server-url https://memory.example.org

# Configure every detected agent, no local memory server (RTK only)
./llm-optimizer.sh --agents all --skip-ai-memory

# Non-interactive, all detected agents, default local ai-memory service
./llm-optimizer.sh --yes
```

Without an explicit `--server-url` (or `AI_MEMORY_SERVER_URL`), the installer sets up a local
systemd user service for ai-memory at `127.0.0.1:49374`; this requires a native Linux executable
and a working systemd user bus. Pass `--service none` to require an already-running server instead.

<details>
<summary>Full option reference</summary>

| Flag | Effect |
| --- | --- |
| `--yes`, `-y` | Configure all detected supported agents non-interactively |
| `--agents claude,codex,agy` | Select specific agents, or `all` |
| `--dry-run` | Plan only — writes the debug log, installs nothing |
| `--report PATH` | Create a new private summary report (also works with `--dry-run`) |
| `--skip-rtk` / `--skip-ai-memory` | Skip one component (skipping both is a no-op) |
| `--install-deps` | Install missing `curl` via a supported package manager |
| `--server-url URL` | Reuse an existing ai-memory server; never creates a local service |
| `--data-dir PATH` | Local ai-memory data directory |
| `--config PATH` | Default: `DATA_DIR/config.toml` |
| `--service auto\|user\|none` | Local systemd user service mode; `none` requires a running server |
| `--enable-linger` | Opt in to service startup without login |
| `--project-strategy basename\|repo-root` | Groups subdirectories/worktrees; omit to keep upstream policy |
| `--routing-target PATH` | Project instruction file target (default: `AGENTS.md`/`CLAUDE.md`) |
| `--ai-memory-version TAG` / `--rtk-version TAG` | Install an explicit release instead of reusing an existing binary |
| `--help`, `-h` | Show usage |

</details>

## What it changes

- Registers ai-memory over MCP and installs its lifecycle hooks for each selected agent.
- Installs RTK's official integration per agent: Claude hooks, global Codex instructions, and
  project Antigravity rules — then adds a short, idempotent command-guidance block to the relevant
  instruction file (`CLAUDE.md` for Claude, `AGENTS.md` for Codex/Antigravity). Reruns update the
  block in place without duplicating it, and changed files are backed up first.
- Every invocation — including `--dry-run` and `--help` — appends a full diagnostic transcript to
  `llm-optimizer.log` in the launch directory (mode `0600`, owned by the invoking user, never
  rotated automatically). Tail it live in another terminal while a run is in progress:

  ```bash
  tail -F llm-optimizer.log
  ```

- Recovery snapshots of every file it touches are written to
  `${XDG_STATE_HOME:-$HOME/.local/state}/llm-optimizer/backup-*`, with a manifest of prior bytes,
  permissions, and symlink metadata for manual restoration.

See [`llm-optimizer.md`](llm-optimizer.md) for the complete configuration, recovery, and validation
reference.

## Testing

```bash
./test.sh -v                                 # uses `rtk test` if installed, else falls back
python3 -m unittest discover -s tests -v     # direct invocation
bash -n llm-optimizer.sh                     # syntax check
```

46 isolated tests cover file preservation, checksum/unsafe-archive handling, official integration
arguments, failure propagation, URL handling, locking, dry-run behavior, private reports, project
snapshots, simulated release download/publish, and simulated systemd start/restart behavior.

Reproduce the RTK output-compression benchmark (requires RTK installed; uses a disposable temp
`HOME`, so it doesn't touch your real RTK config or history):

```bash
python3 benchmark-rtk.py
```

## Project layout

| Path | Purpose |
| --- | --- |
| `llm-optimizer.sh`, `optimizer_support.py` | The installer |
| `llm-optimizer.md` | Full usage, configuration, recovery, and validation notes |
| `llm-optimizer-v2.md` | Earlier v2 specification (historical) |
| `TOOL-DOCUMENTATION-ANALYSIS.md` | Prior documentation review findings |
| `tests/`, `test.sh` | Test suite |
| `benchmark-rtk.py` | Synthetic RTK compression benchmark |

## License

[MIT](LICENSE)
