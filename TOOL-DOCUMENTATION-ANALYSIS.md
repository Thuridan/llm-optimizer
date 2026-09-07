# ai-memory and RTK documentation analysis

Reviewed on 2026-09-06: all 82 files supplied in `docs-ai-memory/` and
`docs-rtk/`: 73 Markdown documents, three executable/data examples, one SVG,
and five PNG assets. This is an analysis of the supplied documentation snapshot,
not certification of installed binaries or current upstream services. Research
reports, old roadmaps, screenshots, and deployment anecdotes are historical
evidence. Detailed operational contracts take precedence over their generalizations.

## How the tools fit together

| Tool | Responsibility | Integration boundary |
|---|---|---|
| RTK | Reduce command output before it enters agent context | CLI proxy, command rewrite registry, agent-specific hooks or instructions |
| ai-memory | Capture bounded session evidence and retrieve knowledge across sessions | Lifecycle capture, MCP, wiki, operational database, optional managed launcher |
| llm-optimizer | Install and connect these components | Must preserve each agent's configuration, command semantics, permissions, and lifecycle |

RTK reduces the immediate cost of reading tool output. ai-memory reduces repeated
discovery across sessions. Neither establishes the correctness of an answer.
Filtering followed by capture and summarization can compound information loss:
memory cannot recover output that was never captured. Exact-source investigation
needs raw reads and verification against the checkout.

## ai-memory: architecture and storage

One Rust server owns lifecycle ingress, MCP, HTTP administration, the read-oriented
browser/API, scope resolution, authentication, and background jobs. Most CLI
commands are HTTP clients. SQLite uses WAL, one writer actor, and a read pool.
The wiki uses git-versioned Markdown under workspace/project UUID directories;
names are metadata, so equal page paths in different projects do not collide.

Markdown is authoritative for wiki content. **SQLite is not wholly disposable**:
sessions, observations, handoffs, identities, credentials, audit history, and
other operational state cannot be reconstructed from the wiki. File installation
and SQLite commit are not one cross-resource transaction; mutation helpers use
ordering, best-effort rollback, and reconciliation for crash recovery.

References: [architecture](docs-ai-memory/ARCHITECTURE.md),
[design rationale](docs-ai-memory/design-decisions.md),
[operations](docs-ai-memory/lifecycle-ops.md).

### Capture and continuity

- Lifecycle hooks capture sanitized, bounded observations. They are not complete
  transcripts. User prompts/compaction summaries and tool excerpts have different
  limits; raw arguments and paths are not universally retained.
- Native hooks use a local spool, bounded delivery attempts, idempotency keys,
  and detached draining. HTTP acceptance is not proof that all downstream work
  completed. Long outages can exceed retry/retention budgets.
- Capture is designed to avoid blocking agent actions. Session-start handoff
  delivery is a bounded synchronous exception. ai-memory capture does not veto
  shell actions; admission webhooks govern memory mutations instead.
- Substantive true session ends produce rule-based summaries and handoffs without
  an LLM. Lifecycle-only sessions produce neither. Optional SessionEnd LLM work
  enters a durable retry queue outside hook latency.
- `Stop` is not universally `SessionEnd`. Codex and Antigravity require explicit
  finalization where their native lifecycle lacks a true end event. Other agents
  have distinct injection points and payloads; support cannot be inferred from
  another client's schema.
- Handoffs are owned, single-use transfers to a future session. Manual handoffs
  take precedence; automatic delivery also considers cwd boundaries and recency.
  They are not a live inter-agent messaging channel.

References: [installation](docs-ai-memory/install.md),
[support matrix](docs-ai-memory/support-matrix.md),
[usage](docs-ai-memory/usage.md), [use cases](docs-ai-memory/use-cases.md).

### Scope and privacy

New hook installs default to `basename(cwd)` unless `--project-strategy repo-root`
is explicitly chosen. Refreshes preserve existing choices. The nearest
`.ai-memory.toml` can select workspace/project/strategy; repo-root handles Git
worktrees through their common repository. Follow-cwd and sticky session routing
are separate choices. Identical repository basenames are not globally unique.

Per-actor auto-scope separates identified sessions, but static MCP clients cannot
magically forward a lifecycle session ID. A session-aware bridge or explicit
scope is needed when their identity is ambiguous. For this project, the current
AGENTS instructions govern tool calls: use automatic current-project scope and
do not supply scope overrides unless the user names another project.

Native `[capture] ignore_paths` is a schema-specific lexical filter applied
before spooling/transport. Shell and PowerShell fallback bundles do not provide
the same enforcement. This is not a general secret detector, shell-content
filter, or symlink-resolving DLP system. Stored text remains untrusted history.

References: [marker file](docs-ai-memory/marker-file.md),
[auto-scope](docs-ai-memory/auto-scope.md), [security](docs-ai-memory/security.md).

### Retrieval and memory lifecycle

Project retrieval combines FTS5, lexical entities, graph neighbors, and available
vectors through reciprocal rank fusion. A bounded authority adjustment uses
page metadata to resolve close relevance contests. Optional LLM reranking is
bounded and falls back to local ordering on failure. Wiki misses can use bounded
observation FTS fallback. CLI search and cross-project `global=true` search have
different FTS-oriented contracts; they are not equivalent to project hybrid
retrieval. Supplemental global preferences are another distinct mechanism.

`memory_read_page` provides full page content; search snippets are not full pages.
Session observation readers are scoped, owner-filtered, paginated, and body-capped.
Feedback attaches to a page version: helpfulness influences episodic retention;
stale/wrong flags feed lint without deleting content.

Working, episodic, semantic, and procedural memory have different retention
roles. Ordinary decay preserves semantic/procedural and pinned pages. Explicit
TTL hides expired pages and allows the sweep to delete them even if pinned.
Slots hold bounded state or invariants; injection policy is not a page ACL.

2.0 additions:

- **Local embeddings:** default best-effort in-process candle inference using
  all-MiniLM-L6-v2, 384 dimensions. Checksum-pinned model files download separately;
  automatic first-start download enables hybrid retrieval after restart.
  Explicit `local` configuration has stricter availability behavior. `none` opts
  out; an explicitly configured provider is preserved. Input truncation limits
  what long page bodies contribute.
- **Vector storage:** packed SQLite vectors and brute-force cosine; `sqlite-vec`
  is deferred. Provider/model/dimension identity prevents mixing incompatible
  vectors. Vector completeness can lag page/FTS persistence and needs backfill.
- **Typed edges:** `causes`, `fixes`, `contradicts`; contradictions feed rule-based
  lint. Retrieval currently treats them as ordinary graph edges.
- **Temporal lookup:** `as_of` runs historical entity lookup alone, using ingestion
  validity windows. It does not reconstruct world-time truth or run historical
  FTS/vector search. Purging destroys the associated timeline.
- **OKF v0.2:** native project bundles with standard frontmatter and ai-memory
  extensions. `export-okf` validates exports; import is through native files and
  indexing. Migration is backup-gated, idempotent, and avoids page-version churn.
  The 2.0.3 notes specifically fix backup timing to precede SQL migration.

References: [local embeddings](docs-ai-memory/local-embeddings.md),
[vector policy](docs-ai-memory/vector-backend-policy.md),
[typed edges](docs-ai-memory/typed-edges.md), [temporal](docs-ai-memory/temporal.md),
[OKF](docs-ai-memory/okf.md), [upgrade](docs-ai-memory/MIGRATION-2.0.md).

### LLM processing and improvement

Generative LLM providers are optional and independent from embeddings. Typed
provider adapters, schema-constrained output, bounded context/output, timeouts,
and narrowly defined compatibility fallbacks are central. Authentication differs
among API, OAuth, subscription, and compatible endpoints; provider settings must
exist in the long-lived server environment, not merely an interactive shell.

With an LLM configured, the background auto-improve scheduler reviews eligible
newly completed sessions. Validated proposals are staged and **auto-approved by
default**. `require_approval=true` and disabling scheduling are independent
controls. Pending sidecar files are frozen staging snapshots; SQLite and
pending-writes commands provide actual proposal status.

Patch proposals have exact heading anchors, hash conflict checks, evidence,
confidence, and size/edit budgets. A rejection buffer discourages repeated bad
proposals. Optional executable eval gates receive JSON on stdin and fail closed
for targeted proposals; commands run directly, not through a shell. The supplied
Python and shell scorers are illustrative structural checks, not semantic
quality guarantees; the shell example does not compare before/after scores.

The opt-in experience pass compares multiple session summaries and requires
cross-session evidence. Curator and improvement telemetry reports do not perform
the maintenance they recommend. Managed routing skills are static prompt packages;
learning does not auto-edit repository rules, executable skills, or source code.

References: [providers](docs-ai-memory/llm-providers.md),
[improvement](docs-ai-memory/auto-improvement-loop.md),
[patch roadmap/status](docs-ai-memory/auto-improve-skillopt-roadmap.md),
[eval gates](docs-ai-memory/auto-improve-eval-gates.md),
[experience](docs-ai-memory/experience.md),
[examples](docs-ai-memory/examples/auto-improve-eval/README.md).

### Managed workstreams

`ai-memory run` adds opt-in cross-harness continuity while preserving each
harness's native session. `show`, `continue`, `resume`, and `workstreams` offer
different discovery/selection flows. Checkout paths are resolved locally; the
server uses workstream identities/fingerprints.

Each workstream has one renewable writer lease and one current native session per
harness. Explicit native selectors win; adopting old sessions is limited to an
otherwise-empty workstream. Adapters read private stores without modifying them,
normalize visible records, omit hidden reasoning/private metadata, and maintain
source and delivery cursors separately. Startup packets are bounded unseen
deltas, not unlimited transcripts. Extraction gaps are reported; Antigravity
deliberately relies on lifecycle capture instead of decoding private trajectory
payloads. Unknown formats must not be guessed.

References: [workstreams](docs-ai-memory/managed-workstreams.md),
[adapter contribution contract](docs-ai-memory/managed-harness-contributions.md).

### Deployment, authentication, extension, recovery

- Run one server per data directory. Keep binary, config, data path, endpoint,
  and credentials consistent between service and clients. Linux systemd,
  macOS launchd, native Windows services, WSL, and Docker have different path,
  account, environment, and networking contracts. Hook bundles must remain
  discoverable after installation.
- Remote deployments need explicit authentication and an appropriate TLS proxy.
  Host allowlists protect against DNS rebinding. Base paths must remain consistent
  across proxy, MCP, hooks, API, and browser routes.
- This is a shared single-tenant wiki with actor attribution, **not per-page
  RBAC**. Owned sessions/handoffs differ from shared pages. Machine-root bearer,
  native user API keys, password sessions, recovery credentials, and trusted
  proxy identities have different capabilities. Disabling human login does not
  revoke API keys; a root user's native key is still User-level.
- Browser sessions use secure cookie/CSRF contracts. `/api/v1` is read-oriented;
  its POST search remains a read but needs CSRF under cookie auth. Custom SPA
  shells can be public while their data stays authenticated.
- Admission webhooks can mutate or reject engine writes before persistence;
  observers run after durable work and can be dropped under bounded overload.
  They are not guaranteed delivery queues. External disk edits bypass admission.
  Consolidation preflight may have an empty body and is not a committed-write
  notification. Handoff admission has distinct latency/degradation behavior.
- Companion software uses public API/MCP mutation paths. Importer support is
  separate from core; the writable web-editor proposal remains undecided.
- Online backup and page checkpoint restoration are distinct from full restore,
  reset, and clean-database reindex, which require stopping the server.
  Project true-moves preserve history; copy-purge merges discard source
  operational history. Session moves gather scattered rows and cannot restore
  the original split simply by moving back.
- Purge is logical deletion. Compaction reclaims live SQLite space but does not
  erase git objects, prior backups, or guarantee forensic erasure.

References: [deploy](docs-ai-memory/deploy.md),
[macOS](docs-ai-memory/macos.md), [Windows](docs-ai-memory/windows.md),
[proxy](docs-ai-memory/https-via-proxy.md), [users](docs-ai-memory/users.md),
[frontend](docs-ai-memory/frontend-api.md),
[admission](docs-ai-memory/admission-webhooks.md),
[companions](docs-ai-memory/companion-crates.md),
[wiki migrations](docs-ai-memory/wiki-migrations.md),
[shell completions](docs-ai-memory/shell-completions.md).

## RTK: execution, filtering, and integration

RTK is a synchronous Rust command proxy. It routes supported commands to compiled
filters or trusted TOML filters, executes the underlying tools, compresses their
output, prints it, and records local savings estimates. Unsupported commands and
`rtk proxy` provide raw execution paths. The underlying tools still need to be
installed. Filters aim to preserve exit status and useful errors; compact output
is not guaranteed machine-readable or semantically identical to raw output.

Strategies include grouping, deduplication, structural summaries, error/failure
focus, tree/progress reduction, and JSON/NDJSON processing. Source-reading filters
can remove comments or bodies, and compact diffs are unsuitable as applicable
patches. Machine consumers and exact review should use raw output.

`rtk rewrite` is the shared command registry; adapters translate agent payloads.
The detailed technical guide documents these statuses:

| Exit | Meaning |
|---|---|
| 0 | Rewrite with Allow |
| 1 | No equivalent rewrite |
| 2 | Deny |
| 3 | Rewrite with Ask |

Consequently, nonzero is not uniformly failure, and stdout alone does not convey
the permission decision. A wrapper must preserve the distinction and the native
agent's approval behavior. Filtering is not a command safety boundary.

The technical guide describes lexer-based handling of compound commands, with
conservative pipeline/heredoc/arithmetic exceptions. The official Codex and
Antigravity paths in this snapshot provide instructions; our executable rewrite
hooks are custom integrations. Other agents have native hook/plugin support.

Configuration covers tracking, display, filters, tee, telemetry, and exclusions.
Project/global TOML filters require content-hash trust; hook integrity is separately
checked. Exclusions account for wrappers and environment prefixes but have
documented matcher gaps. Do not mistake an exclusion list for a security policy.

Tee stores bounded raw failure output for recovery, subject to minimum size,
maximum file size, count, and mode. The saved output can be truncated. `gain`
reports observed command reductions; `discover`, `session`, and `learn` analyze
supported agent history and missed opportunities, with agent-specific coverage.
Token estimates use approximately bytes/4. Compression percentages and cost
estimates are not measured billed-token savings or end-to-end task efficiency.

Dedicated telemetry docs specify explicit opt-in, bounded aggregate payloads,
asynchronous sending, and separate disable/forget operations. They distinguish
remote aggregate telemetry from local tracking, which can contain command text.

References: [technical contract](docs-rtk/contributing/TECHNICAL.md),
[architecture](docs-rtk/contributing/ARCHITECTURE.md),
[features](docs-rtk/usage/FEATURES.md),
[configuration](docs-rtk/guide/getting-started/configuration.md),
[agents](docs-rtk/guide/getting-started/supported-agents.md),
[tracking](docs-rtk/usage/TRACKING.md), [telemetry](docs-rtk/TELEMETRY.md),
[audit guide](docs-rtk/usage/AUDIT_GUIDE.md).

## Evidence quality and documentation conflicts

The ai-memory benchmark snapshot reports LongMemEval-S hit@5 moving from 0.617
to 0.668 with FTS changes and to 0.823 with local embeddings. It scores 470
questions, excludes 30 abstention questions, and models bounded production
capture. Hit@5 means any evidence session found; recall@5 means the fraction of
evidence sessions found. Neither is answer accuracy. Cross-system comparisons
remain sensitive to capture, dataset, and metric definitions.

The provider comparison uses five synthetic fixtures and manual quality review.
Its useful findings concern schema correctness, faithfulness, restraint, and
reasoning/output budgets. Its prices, model rankings, and claims that a provider
is universally unsuitable are not current guarantees.

Historical research explains the engineering choices: single-writer durability
instead of competing writers; explicit scope dimensions; typed provider adapters;
structured output; bounded capture/retention; safe recovery. Agentmemory,
basic-memory, Cognee, and MemPalace reports are dated tracker analyses, not
current independent competitor audits. ECC concerns workflow/governance;
codebase-memory-mcp concerns code structure. Their proposed tool tiers, config
scanner, rule-promotion flow, and shared index are not established shipped
ai-memory features in this snapshot.

Conflicts to retain explicitly:

| Topic | Conflicting documentation | Interpretation |
|---|---|---|
| ai-memory embeddings | Historical off-by-default/ONNX future vs 2.0 candle default; even local-embeddings retains an old opt-in sentence | Follow detailed 2.0 startup/override contract; verify binary before configuring |
| DB recovery | General “derived/rebuildable” descriptions vs operations' DB-only state list | Back up full state; reindex cannot restore sessions/auth |
| Client support | Some MCP/support sections label integrations deferred while later sections describe shipped behavior | Verify selected client/version and payload, not a blanket support claim |
| macOS | Symlink discovery marked fixed in 1.39 but old troubleshooting remains; bearer env-only claim conflicts with users' auth config | Treat old sections as stale and inspect actual config contract |
| OKF/import/logs | Old proposed command names and no-log claims vs concrete migration/operations guides | Use concrete command reference; do not execute research examples blindly |
| Atomicity/moves | Broad atomic/reversible labels vs explicit crash, partial failure, and merge-loss descriptions | Preserve the detailed limitations |
| RTK rewrite | Older agent page disallows compounds vs technical lexer contract | Version-sensitive; our conservative skip is a custom policy |
| RTK telemetry | Generic enabled=true example vs dedicated explicit-opt-in docs | Preserve consent; do not infer defaults from sample config |
| RTK tracking | history.db vs tracking.db, retention and WAL descriptions differ | Discover actual configured path/schema before maintenance |

References: [benchmarks](docs-ai-memory/benchmarks/README.md),
[provider comparison](docs-ai-memory/llm-provider-comparison.md),
[prior-art synthesis](docs-ai-memory/prior-art-implementation-findings.md),
[2026 research](docs-ai-memory/research-2026-landscape.md),
[2.0 roadmap](docs-ai-memory/ROADMAP-2.0.md),
[historical roadmap](docs-ai-memory/v0.3-roadmap.md).

## Implications for this installer

These are review findings and next implementation targets, not changes made by
this documentation analysis:

1. **Config/service consistency:** the script initializes with an explicit
   `~/.config/ai-memory/config.toml`, but its generated service omits `--config`.
   The documented default config lives inside the data directory. Align service
   and CLI config/data arguments, endpoint selection, and persistent provider env.
2. **Release layout:** retain/discover the complete hook bundle when installing
   native archives. Copying only the executable can break clean hook installation
   or reuse stale cached hooks.
3. **Project routing:** the script invokes `install-hooks` without the repo-root
   option that our project document claims it uses. Make the intended choice
   explicit while preserving existing user configuration.
4. **Lifecycle guidance:** explain Codex finalization as well as Antigravity;
   correct the claim that no LLM means no summaries or handoffs.
5. **RTK permission semantics:** the custom Python hook judges rewrite stdout and
   emits Allow. Revisit it against documented Allow/Ask/Deny statuses and the
   actual agent contract before treating it as permission-preserving.
6. **Configuration preservation:** include symlink-target preservation alongside
   existing atomic writes, backups, malformed-input checks, and foreign-hook
   preservation. Atomic replacement alone can replace a symlink itself.
7. **Installation completeness:** separately verify MCP wiring, lifecycle capture,
   routing instructions/skills, rewrite behavior, and session finalization. A
   reachable HTTP endpoint or successful settings write proves only one layer.
8. **Claims:** distinguish measured host behavior from upstream support, bounded
   observations from transcripts, and output compression from billing savings.

No installer execution, live configuration change, provider call, or ai-memory
wiki mutation was performed for this review.
