# Changelog

All notable changes to this project are documented in this file. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project does not yet
follow strict semantic versioning guarantees.

> This git repository was initialized from an already-developed codebase. Entries
> below 2.1.0 are reconstructed from in-repo documentation
> ([`llm-optimizer-v2.md`](llm-optimizer-v2.md)) rather than commit history, so exact
> dates aren't available for them.

## [2.1.0] - 2026-09-07

### Added

- Short, idempotent RTK command-guidance block installed into each agent's instruction
  file after its official initializer runs: Claude's global `CLAUDE.md`, Codex's global
  `AGENTS.md`, and Antigravity's project `AGENTS.md`. Reruns update the block in place
  without duplicating it; changed files are backed up first. A malformed ownership
  marker fails the run instead of risking an ambiguous edit.
- `./test.sh` runner: uses `rtk test` when installed, falls back to plain
  `python3 -m unittest`, works from any launch directory, and preserves the underlying
  exit status.
- `benchmark-rtk.py`: reproducible synthetic RTK output-compression benchmark, run in a
  disposable temp `HOME` so it never touches host RTK configuration or history.

### Changed

- Validated against RTK v0.48.0: 40 passing `unittest` cases compress from 1,919 raw
  bytes to 127 filtered bytes (93.4% reduction); with one injected failure, 2,495 raw
  bytes compress to 185 (92.6% reduction) while exit status and the failure diagnostic
  are preserved.

### Fixed

- Installation subprocesses now receive EOF on stdin, so an invisible RTK telemetry
  consent prompt can no longer block or silently consume terminal input (previously
  observed against RTK v0.48.0 during a live host installation).
- Claude's config directory is now created when absent instead of failing the Claude
  integration step.

## [2.0.x] - historical

### Changed

- Replaced the installer's custom Codex rewrite adapter with each tool's official
  integration path: MCP registration for ai-memory, and Claude hooks / global Codex
  instructions / project Antigravity rules for RTK. The custom adapter's 15 tests were
  replaced with tests for the official integrations, file preservation, and
  installer orchestration (46 tests total as of 2.1.0).

### Fixed

- The removed custom Codex rewrite adapter had discarded RTK's typed permission
  decision (0=Allow, 1=no-op, 2=Deny, 3=Ask), always emitting `Allow` regardless of the
  underlying result — silently overriding legitimate `Deny`/`Ask` outcomes. Moving to
  the official integration path removes this class of bug rather than patching it.
- Re-registering a hook after its target path changed previously left both the old and
  new entries in place; checksum verification also silently accepted downloads with no
  expected hash. Both were addressed as part of the same rewrite.

## [1.0.2] - historical

Baseline version reviewed in [`llm-optimizer-v2.md`](llm-optimizer-v2.md). Used a
custom Codex rewrite adapter (later removed, see 2.0.x above) and had not yet added
`optimizer_support.py` or the current test suite.
