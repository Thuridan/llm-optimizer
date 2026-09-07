# llm-optimizer

A Linux installer that wires up [ai-memory](https://github.com/akitaonrails/ai-memory) (persistent
cross-session knowledge via MCP and lifecycle capture) and [RTK](https://github.com/rtk-ai/rtk)
(token-efficient shell-command output) for locally installed Claude Code, Codex, and Antigravity
agents.

## Quick start

```bash
./llm-optimizer.sh --help
./llm-optimizer.sh --dry-run --agents codex
./llm-optimizer.sh --agents codex --server-url https://memory.example.org
```

Run it as the account that launches your agents, from the project whose instructions you want to
configure. Keep `optimizer_support.py` next to `llm-optimizer.sh` — the installer depends on it.

Requirements: Linux, Bash 4+, Python 3.8+, `flock` and `timeout`. `curl` is needed for release
downloads and the local health probe.

## Layout

- `llm-optimizer.sh` / `optimizer_support.py` — the installer.
- `llm-optimizer.md` — full usage, configuration, recovery, and validation notes.
- `llm-optimizer-v2.md` — earlier v2 specification (historical).
- `TOOL-DOCUMENTATION-ANALYSIS.md` — prior documentation review findings.
- `tests/`, `test.sh` — test suite (`./test.sh -v`).
- `benchmark-rtk.py` — synthetic RTK output-compression benchmark.
- `rtk-docs/` — vendored RTK documentation.

## Tests

```bash
./test.sh -v
# or directly:
python3 -m unittest discover -s tests -v
```

See [`llm-optimizer.md`](llm-optimizer.md) for full documentation, including configuration
options, recovery snapshots, the debug log, and validation results.
