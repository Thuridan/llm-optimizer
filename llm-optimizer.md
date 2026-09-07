# llm-optimizer v2.1.0

This Linux installer connects ai-memory (persistent session knowledge through
MCP and lifecycle capture) and RTK (compact shell-command output) to installed
Claude Code, Codex, and Antigravity agents. Keep `optimizer_support.py` beside
`llm-optimizer.sh`. Run it as the account that launches your agents, from the
project whose instructions you want to configure.

## Run

```bash
./llm-optimizer.sh --help
./llm-optimizer.sh --dry-run --agents codex
./llm-optimizer.sh --agents codex --server-url https://memory.example.org
```

Replace the example endpoint with your ai-memory server. Without an explicit
endpoint or `AI_MEMORY_SERVER_URL`, installation prepares a local systemd user
service at `127.0.0.1:49374`. It requires a native Linux executable and a working
systemd user bus. `--service none` requires an already running server.
`--enable-linger` explicitly enables service startup without login.

`--agents all` selects detected executables; `--yes` suppresses interactive agent
selection. `--skip-ai-memory` and `--skip-rtk` select components; skipping both
does nothing. Existing executables are reused after capability checks. Explicit
`--ai-memory-version TAG` / `--rtk-version TAG` request a release installation.
Missing executables use the release API to resolve a single version per component.

Requirements: Linux, Bash 4+, Python 3.8+, flock and timeout for installation.
Curl is needed for release downloads and the local listener probe, but not for
RTK configuration or reuse of an explicit memory server with existing binaries.
`--install-deps` permits installing missing curl through a supported package manager.

## Configuration and recovery

- Data defaults to `$AI_MEMORY_DATA_DIR`, then
  `${XDG_DATA_HOME:-$HOME/.local/share}/ai-memory`; configuration defaults to
  `DATA/config.toml`. Override with `--data-dir` and `--config`.
- Explicit server URLs accept a base path or a trailing `/mcp`; remote endpoints
  require HTTPS. Supply authentication through the upstream configuration or
  environment, rather than embedding credentials in the URL.
- MCP registration precedes hooks. `--project-strategy repo-root` explicitly
  groups subdirectories/worktrees; omission preserves upstream installation policy.
- Project routing defaults to `CLAUDE.md` for Claude and `AGENTS.md` for
  Codex/Antigravity. `--routing-target PATH` chooses one target instead.
- RTK uses its official integrations: Claude hooks, global Codex instructions,
  and project Antigravity rules. Exact legacy optimizer RTK registrations are
  removed for Codex/Antigravity. No custom rewrite adapter is installed.
- Verified archives retain their complete layout in
  `${XDG_DATA_HOME:-$HOME/.local/share}/llm-optimizer/releases`. Publication uses
  links in `~/.local/bin`; unmanaged binaries there are preserved. Agent launch
  environments must include this directory on PATH.
- Recovery snapshots live in
  `${XDG_STATE_HOME:-$HOME/.local/state}/llm-optimizer/backup-*`.
  Each manifest records selected agent configuration files, project routing
  targets, and RTK's Antigravity rules file, including prior bytes, permissions,
  and symlink metadata. The snapshot location is also included in an explicit
  report. Inspect the manifest before manually restoring a file or its symlink
  target. Snapshots are not automatic rollback or a complete backup of generated
  skills, hook bundles, server configuration, or memory databases.

The upstream routing installer also manages skills alongside instructions.
See the [ai-memory installation contract](https://github.com/akitaonrails/ai-memory/blob/main/docs/install.md)
and [RTK integrations](https://github.com/rtk-ai/rtk#supported-ai-tools).

`--dry-run` does not execute agents, services, installers, downloads, or analytics.
It does write the automatic debug log. `--report PATH` additionally creates a
new private summary report; existing report files and symlinks are rejected.

## Administrator debug log

Every invocation appends to **`llm-optimizer.log` in the launch directory**,
including dry-run, help, and argument/preflight failures. Existing contents are
preserved. Each run has start/end markers and its final exit status. UTC timestamps,
step descriptions, command output (stdout and stderr), durations, downloads, and
health-check attempts are recorded as output is emitted. Partial lines and prompts
are captured without waiting for a newline. The terminal shows script prompts, progress, step results, and the final summary.
Capability help and detailed subprocess stdout/stderr appear only in the log.
Upstream programs can still buffer their own output before emitting it.

```bash
# In another terminal, from the same directory:
tail -F llm-optimizer.log
```

The log is owned by the invoking user with mode `0600`. Symlinks, hard links,
nonregular files, and files owned by another user are rejected. A lock prevents
concurrent runs from interleaving this log. If the log cannot be opened or written,
the invocation fails. No shell tracing or environment dump is used, but upstream
command diagnostics can contain sensitive data; treat the file as private.
The log is retained after errors and has no automatic truncation or rotation.
Archive it between runs as needed; do not rotate it during an active run.

Installation subprocesses receive EOF on stdin so prompts cannot block or consume
terminal input. They time out after 180 seconds, with forced termination after
another 5 seconds if necessary. Existing telemetry consent is preserved; the
installer does not opt in on your behalf. On failure, inspect the command's output
before its `DONE`/`ERROR` entries in the debug log. `--report` remains a summary;
`llm-optimizer.log` is the detailed diagnostic transcript.

## Validation performed on 2026-09-07

```bash
./test.sh -v
# Without RTK: python3 -m unittest discover -s tests -v
bash -n llm-optimizer.sh
./llm-optimizer.sh --dry-run --agents codex
```

The suite has 46 isolated tests. It covers file preservation, checksums and unsafe
archives, official integration arguments, failure propagation, URL handling,
locking, dry-run behavior, private reports, project snapshots, simulated release
download/publication and repeat installation, and simulated systemd start versus
restart behavior. Logging tests check append/private-file behavior, failure status,
unsafe destinations, and partial output visible before a command finishes.
External programs are fixtures; these tests do not certify
upstream binary behavior or a live daemon.

The actual host dry-run succeeds. A real RTK installation was also attempted in
a disposable home: GitHub API DNS resolution failed, the installer returned 1,
and no executable was published. The operator subsequently installed ai-memory v2.1.0 and RTK v0.48.0 on the host.
That run exposed an invisible RTK telemetry prompt; v2.0.2 closes subprocess stdin
to prevent the wait and creates the Claude config directory when absent.
The installed RTK v0.48.0 completed all three integrations twice in a disposable
home after the fix, each run taking about one second. The expected Claude hooks,
Codex instructions, and Antigravity rules were created. Agent executables were
fixtures for discovery; live server health, agent loading, capture, and
finalization remain unverified by this test suite.

After installation in a connected environment, restart the selected agents and
verify MCP `memory_status`, capture and retrieval in a disposable project/session.
Verify actual permissions and RTK output against a raw command. Use `rtk proxy`
when exact output is needed. Confirm explicit session finalization where the
agent lacks a true SessionEnd event. Successful installer commands prove that
configuration was applied, not that an agent loaded or exercised it.

The older [v2 specification](llm-optimizer-v2.md) and
[documentation analysis](TOOL-DOCUMENTATION-ANALYSIS.md) preserve historical review
findings. Their referenced `docs-ai-memory/` and `docs-rtk/` snapshots are absent
from this checkout; use the upstream links above for available documentation.

## RTK optimization in v2.1.0

Each RTK integration now installs a short managed command-guidance block after
running the official initializer. Claude receives it in its global `CLAUDE.md`,
Codex in its global `AGENTS.md`, and Antigravity in the current project's
`AGENTS.md`. Repeated runs update the block without duplication, preserve
unrelated instructions and symlinks, and back up changed files. Malformed
ownership markers cause an error instead of an ambiguous edit.

The guidance selects dedicated filters, compresses generic unittest output,
narrows searches, uses minimal file reads for exploration, and requires exact
reads for implementation work. Failure recovery and exit-status checks remain
explicit. It does not alter approval hooks, opt into telemetry, trust custom
filters, or impose unmeasured output limits. Ultra-compact initialization did
not persist in the tested Claude hook and showed no extra savings in the sample
Git-status workload, so it is not advertised as a global optimization.

For this repository, run tests with:

```bash
./test.sh -v
```

The runner uses `rtk test` when installed, falls back to Python unittest otherwise,
and preserves the runner's exit status. It works from any launch directory.
Detailed failure output is available through RTK's existing recovery files.

To reproduce the synthetic compression benchmark without changing host RTK
configuration or tracking history:

```bash
python3 benchmark-rtk.py
```

Measured with RTK v0.48.0: 40 passing tests produced 1,919 raw bytes versus 127
filtered bytes (93.4% reduction). Adding one intentional failure produced 2,495
versus 185 bytes (92.6% reduction); exit status 1 and the diagnostic marker were
preserved in filtered output or the recovery file. These are synthetic output-byte
measurements, not estimates of total task tokens or billed savings.

Project `AGENTS.md` and `CLAUDE.md` were updated in this workspace. To apply the
new global guidance on the host, run:

```bash
./llm-optimizer.sh --agents all --skip-ai-memory
```

Restart agents afterward. Global home-directory configuration could not be changed
from the coding sandbox. Review adoption after representative work with
`rtk discover --since 7`, `rtk gain --weekly`, and `rtk gain --failures`; current
Claude discovery history is too small to establish host-wide savings.
