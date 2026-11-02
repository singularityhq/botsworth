# Botsworth — Design

(Botsworth was called Agent Profiler while it was designed. References to `approaches.md`, `design-model.md`,
`design-scenarios.md` and other planning drafts point to documents that are not part of the public repository; the
specs in `docs/specs/` and the decision log in `decisions.md` carry everything those drafts settled.)

Status: v4 build landed on main, 2026-10-13 (schema v7). This file is a map: it states the model, names
the module that owns each contract, and points at the source of truth for detail. It does not copy the specs.

- Specs (source of truth for behaviour): `docs/specs/data-model.md` (DM-n, schema v7), `docs/specs/interfaces.md`
  (QRY-n, API-n, CLI-n, TST-n), `docs/specs/ui/*.md` (explorer, session, turn, detail, system).
- Decisions: `decisions.md` here holds D1–D16. The full decision log (D1–D51) and the build run log are not in this repo.
- Code contracts: the CONTRACT docstring at the top of each file under `src/botsworth/` (GUARANTEES, EXPECTS,
  FAILURE BEHAVIOR, DOES NOT). Where this file and a docstring disagree, the docstring is the one the tests check.
- Canonical stream contract: summarised in §6.4 and `src/botsworth/stream.py`; the builders' contract file is not in this repo.
  Candidates and benchmark: `approaches.md`. Model and scenarios: `design-model.md`, `design-scenarios.md`.

## 1. Summary

Three coding-agent harnesses (Claude Code, Codex, omp) write append-only session files with different shapes.
A developer or an agent needs to know where time, tokens and money went: in one session, across a project's
sessions, and over time. The profiler normalizes every file into one **attribution tree of operations**
(session → turn → LLM call → tool call → engagement → …). Only `llm_call`/`tool_call` own metrics; every
container's totals are the sum of its subtree. Time is per op and combined by span and union, never by sum.
Every metric and causal link carries provenance.

Beside the tree: **steps** (display units per turn), **events** (compaction, api errors, limit telemetry, skill
invocations) and **content references** (`src://` URIs into the source files, which stay the only home of text).

v4 build, as it stands on main:

- **Ingest** (`botsworth ingest`): provider files → adapters → bundles in memory → SQLite store (schema v9; no streams folder, D52).
  Explicit, bundle-atomic, never triggered by a query (§8).
- **Store**: spans plus small metadata, previews capped at 160 characters, long content behind references
  (D39, D43). Local-zone day/hour/week rollups, per-turn attributed cost, time split and no-activity
  stretches are computed at ingest (§7).
- **Query layer** `botsworth.query`: one Python package; HTTP and CLI only parse arguments and call it (§5).
- **Two consumers**: the React UI (`botsworth serve`, Azure-style blades: explorer → session → turn → detail,
  D36) and a JSON-by-default CLI for agents, including `slow`, `breakdown`, `tasks` (D35, D42, §6).

## 2. Key decisions

| # | Decision | Why (one line) | Record |
|---|---|---|---|
| D1 | One attribution tree; time per op, not inherited | §8 questions ask "which child accounts for" | decisions.md D1 |
| D2 | Only `llm_call`/`tool_call` own metrics; steps and events own none | Double counting structurally impossible | D2 |
| D3 | `engagement` (burst caused by one tool call) is the node; `agent_id` an attribute | Re-messaged agents attribute per message | D3 |
| D4 | Provenance on metrics (`present/derived/estimated/missing`), identity, and causal links | omp `eval` spawns are time-linked (62 of 423) | D4 |
| D5 | Cost: `reported` → `computed` (models.dev snapshot, fingerprint, aliases) → `missing` (null, excluded, lower-bound) | the maintainer D6/D9; subscription usage is `computed` = API-equivalent (D8), `account` says who paid | D5 |
| D6 | One occurrence, one owner, corpus-wide; copies `duplicate_of` | Claude 1,135 cross-file requestIds; Codex forks copy ancestors | D6 |
| D7 | Explicit `botsworth ingest`; bundle-atomic; responses carry freshness. Append-only tail resume was not built: a changed bundle is rebuilt whole (§8) | the maintainer D5 | D7, D15 |
| D8 | Events are timestamped rows attached to an op, never ops | Containers with own metrics would break D2 | D8 |
| D9 | CLI with JSON + importable Python API over one query layer; HTTP for the UI; no MCP now | the maintainer D3 | D9 |
| D10 | Tool-call token attribution deferred | Estimated and complex | D10 |
| D11 | Store holds metadata, metrics, view info; text behind `src://` pointers (stored compactly since D39/D43) | the maintainer D4 | D11 |
| D12 | Project = session's first `cwd`; per-record `cwd` kept; no alias table now | the maintainer D10 | D12 |
| D13 | Steps layer with a provider-neutral action taxonomy; deterministic, no LLM | the maintainer R-steps | D13 |
| D14 | `account` dimension on sessions, calls and events (window modelling dropped for V1 by D16) | Codex `used_percent` includes omp usage | D14 |

D15–D27 are entries in the maintainer log, cited inline where they apply (skills and limits scope D15–D16, `command_key` D17,
depth ceiling D18, `segments` D19, store location D20, missing store D22, owner rank D23, `busy_sum` D24,
`source_files` status D26). Full text: `decisions.md`.

v4 decisions (id and choice; the reason and limitations are in `decisions.md`):

| # | Choice |
|---|---|
| D28 | Question-first UI. "Quota" means usage: tokens and API-price cost over a period the user picks. No 5 h windows |
| D29 | Explorer-first layout (period chart, filters, session list on one page). Facts only: no "why it stands out" chips, no generated sentences |
| D30 | Session list is Rows; `refine-ui` governs every UI brief |
| D31 | UI system rules: Lucide 16 px icons, 4-size type scale, ink3 `#838ba3`, money with 2 decimals in tabular figures, model glyph marks, one look per fact |
| D32 | Explorer: stacked day chart, project donut and ranked list; flat ledger list showing full session cost; automated sessions hidden by default |
| D33 | Session, turn and detail are three drill-down blades; the turn blade toggles Ledger and Waterfall over one row list |
| D34 | Display data rules: title order (rename, generated, first ask of ≥ 4 words, `project · id8`); harness turns fold away; idle time is no-activity stretches; turn cost includes agents it caused |
| D35 | One `botsworth` CLI: `ingest`, `serve`, agent query commands (JSON by default, `--table` for humans). No GraphQL |
| D36 | Continuous blades (Miller columns), no spines |
| D37 | Spec rulings: stretches ≥ 60 s with a `state`; turn Wall/Waiting are span-based; one store time zone (`meta.tz`; superseded by D50: zone per request); four `title_source` values; full rebuild by swap with an ingest banner; extra indexes |
| D38 | Cross-check rulings: a turn's span is its stored `[start_ms, end_ms]`; legend jumps go to the longest row; no automatic refetch; `data-model.md` §2.2 is the only DDL source; `find` default limit 2000 |
| D39 | Content references and minimal storage: `src://` is the only way to long content; prompts, thinking and tool I/O are never stored; previews ≤ 160 chars; v7 store ≤ 50 % of v6; builds use a separate store via `--db` |
| D40 | Time gaps are shown as gap rows (option A) |
| D41 | Re-check rulings: finder chip error state; week buckets for periods over 92 days; `before_first` is its own label; turn axis includes the agent tail |
| D42 | Tokens by task split a call's tokens evenly over the tool calls it issued, named in a `basis` field; wire `command` is the 160-char preview, full text via `command_src` |
| D43 | Refs stored as (`src_files` id, locator, hash); nested-set `pre`/`post` replaces `op_closure`; `occ_hash` replaces `occ_key`; `command_norm` stored whole; Codex thread title is a fallback ask |
| D44 | `struct-soft` alpha .10 → .08 so ink3 text on an open row meets 4.5:1 |
| D45 | Cost-bar scale uses `stats.max_cost_usd` over all in-scope rows; a toast does not block keys |
| D46 | The DM-45 attribution walk ends by revisit detection and a "cause is a tool_call" check, not by time order |
| D47 | Histogram bins carry `main_cost_usd` so a brushed range splits main thread from agents |
| D48 | A resume gap inside a main turn's span counts as `none` in the span figures |
| D49 | CLI `--project` resolves key, name or key suffix; no match or several is an error; `slow`/`breakdown`/`tasks` `--help` defines the output fields |
| D50 | Stored times are epoch ms only: no `meta.tz`, one 15-min epoch rollup, schema 8; the zone is a query parameter (`Scope.tz`, `--tz`, browser zone); CLI output adds ISO and readable siblings (Q31 A) |
| D51 | The explorer header labels its figures "Whole sessions active in this period"; the day-bar tooltip reads "Calls on <day>" |
## 3. Needs review

- No question to the maintainer is open (HANDOFF.md). Deferred items, in `PLAN.md` run log and gates: TASK-ATTR
  (`tasks --by slash|skill` skew from `/compact`), TBL-UNITS, SPEC-WAITS (`waits_on_user` is 1 or NULL), fix-list item 7,
  and DM-90 (a parser-version bump alone does not rebuild bundles; use `ingest --force`).
- Facts assumed since v3 and not changed by the v4 build: F8 (Codex child threads inherit the parent's total as baseline;
  rule in §4.4) and F17 (real prices from the models.dev snapshot).
- Ignored scenarios: 8 (`design-scenarios.md`).

## 4. Model — the intermediate format

### 4.1 Invariants

1. **Tree.** Every op except a `session` has one parent; `root` is the session; no cycles. Each nested agent adds four levels (tool_call → engagement → turn → llm_call), so max op depth = 4k + 3 for k nested agents; real data has k = 4 (session b8c9d0e2, depth 19). Ceiling 32 (D18) is a runaway guard only.
2. **Leaf ownership.** Own metrics (tokens, cost, `dur_ms`) are non-null only on `llm_call` and `tool_call`. Containers, steps and events own nothing.
3. **Inclusive = Σ own over the subtree**, excluding ops with `duplicate_of`. `Σ children incl + own = incl` at every node.
4. **Time is not additive.** Own intervals are the `[start, end]` of `llm_call`/`tool_call` ops. `wall_ms` = length of the union of the subtree's `segments` (D19; a file's single `[start, end]` when it has no resume); `active_ms` = union length of own intervals; `busy_sum_ms` = Σ own `dur_ms`; `busy_sum ≥ active ≤ wall`. Resume gaps between segments are idle, never active, and never inside `wall_ms`.
5. **One occurrence, one owner.** An `occ_key` maps to exactly one owning op corpus-wide. Owner order (common.md F9): session first-line timestamp; within a session, lower `spawnDepth` first, normal transcripts before forks, then first timestamp. Ownership does not depend on ingest order.
6. **Provenance travels with the number and with the link.** `missing` metrics are null, never 0. Inclusive provenance is the weakest in the subtree; `cost_lower_bound = true` when any leaf cost is `missing`.
7. **Text never enters the store.** Every op, step and event carries a content reference; the store holds labels, summaries ≤ 120 and snippets ≤ 160 chars, and numbers (D39; DM-13). One exception: `ops.command_norm`, the D17 grouping key, is stored whole (D43).
8. **Steps are display units.** A step references the op that owns its cost/time; steps are never summed for cost.

### 4.2 Stream records and the op schema

The canonical stream (common.md §2) is the shape of a `Bundle`'s records. Since D52 the pipeline keeps it in memory; `stream.write_bundle` writes a file only for tests, fixtures and the Claude adapter's `run --out` dev command. One JSON object per line in such a file, `rec ∈ op | step | event | source_file`. Field names below are the contract for all three. Integer epoch ms UTC. "Provenance" values are `present | derived | estimated | missing`.

```jsonc
// rec = "op"
{
  "rec": "op", "op_id": "...",                 // "{provider}/{session_id}/{kind}/{key}" (§4.3)
  "kind": "session|engagement|turn|llm_call|tool_call",
  "parent": "<op_id>|null", "seq": 0, "depth": 0,
  "caused_by": "<op_id>|null",                 // engagement: the spawning/messaging tool_call
  "caused_by_basis": "id|content_hash|name|time-heuristic|none",
  "provider": "claude_code|codex|omp", "session_id": "...", "agent_id": "...|null", "engagement_ix": 0,
  "project": "/abs/first/cwd", "cwd": "/abs/cwd/of/this/record",      // D10/D12
  "account": "anthropic:default | openai:plus | openai:cred-<id> | ...", // D14
  "agent_type": "...", "agent_name": "...", "spawn_depth": 0,
  "occ_key": "...|null", "duplicate_of": "<op_id>|null",
  "start_ms": 0, "end_ms": 0, "dur_ms": 0, "time_prov": "present|derived|estimated|missing",
  "ttft_ms": null, "ttft_prov": "missing", "is_async": false,
  "segments": [[0, 0]],                        // containers only (D19): per-file spans split at resume events; adapters never union across files
  "in_tok": 0, "cache_read_tok": 0, "cache_write_tok": 0, "cache_write_5m_tok": 0, "cache_write_1h_tok": 0,
  "out_tok": 0, "reasoning_tok": 0, "context_tokens": 0,              // context = in + cache_read + cache_write
  "tok_prov": "present|missing",
  "cost_usd": null, "cost_in_usd": null, "cost_out_usd": null, "cost_cache_read_usd": null, "cost_cache_write_usd": null,
  "cost_basis": "reported|computed|missing", "price_alias": "gpt-5|null", "price_fingerprint": "...",
  "cut_off": false, "stop_reason": "...", "error_kind": "rate_limit|overloaded|aborted|error|null",
  "model": "...", "model_context_window": null,
  "tool_name": "...", "command": "...", "command_norm": "full normalized command", "command_key": "program + subcommand head incl. launcher, e.g. uv run pytest", "file_path": "...", "exit_code": null, "exit_code_prov": "derived",
  "is_error": false, "interrupted": false, "result_bytes": 0, "role": "main|aux",
  "label": "≤120 chars", "args_json": "≤2 KB",
  "src": "src://..."                           // §4.9
}
```

**Superseded by schema v7 (D39, D43; `data-model.md` §2.2–2.3, DM-8, DM-14, DM-15).** The stream still carries the fields above. The store
columns differ: `src` is stored as (`src_file`, `src_loc`, `src_h`); `occ_key` as `occ_hash` (8 bytes of SHA-256); `parent` and `caused_by` as
`parent_rowid` and `caused_by_rowid`; `cwd`, `account`, `path_sig` as interned `names` ids; `session_id`, `bundle`, `project` are read through
`sessions`; `args_json` is not stored; `command` and `file_path` are clipped to 160 chars. `pre`/`post` (nested set) replace `op_closure`. The view
`ops_v` returns the v6 column names for readers that have not moved. Store tables, in v3 terms:

- `ops` — the record above plus `rowid`, `root_rowid`, and, per engine, the derived columns `incl_{in,cache_read,cache_write,cache_write_5m,cache_write_1h,out,reasoning}_tok, tok_total, incl_cost_*_usd, incl_llm_ms, incl_tool_ms, wall_ms, active_ms, busy_sum_ms, blocked_ms, idle_ms, max_parallel, n_llm_calls, n_tool_calls, n_engagements, n_errors, n_steps, n_duplicates, incl_tok_prov, incl_time_prov, cost_lower_bound`.
- `steps` — §4.10. `events` — §4.11. `source_files` — path, provider, bundle_id, size, mtime_ns, sha_first_4k, lines_ingested, bytes_ingested, parser_version, ingested_at_ms, status (`live|settled|source_missing`).
- `sessions` — one row per session op: `provider, session_id, project, account, cwd_first, git_branch, title, harness_version, entrypoint, originator, first_ms, last_ms, status, check_cost_usd, check_api_ms, check_api_no_retry_ms, check_tool_ms` (Claude `cost-state`), `n_malformed, n_duplicates`, plus the session op's inclusive columns — the cross-session pre-aggregate.
- `accounts` — `account, provider_family, plan, credential_id, window_5h_anchor_ms, window_week_anchor_ms, anchor_basis`.
- v7 adds `stretches`, `projects`, `src_files`, `names` and the columns (v9 drops `stream_files` and adds `source_files.reject_reason`) listed in §7. `price_table` (models.dev snapshot: `model_id, provider, snapshot_date, in_usd_m, out_usd_m, cache_read_usd_m, cache_write_5m_usd_m, cache_write_1h_usd_m`), `model_aliases` (`alias → model_id, provider`; D11: `gpt-5-codex→gpt-5`, `gpt-5.1-codex→gpt-5.1`, `gpt-5.2-codex→gpt-5.2`; codex-max/-mini/auto-review have none → `missing`), `meta` (`schema_version, parser_version, price_fingerprint, last_ingest_ms`).

### 4.3 Operation ids

`op_id = "{provider}/{session_id}/{kind}/{key}"`.

| kind | key | id_prov |
|---|---|---|
| session | `-` | natural |
| engagement | `{agent_id}#{engagement_ix}` | natural |
| turn | `{agent_id or main}#{ordinal}` | derived |
| llm_call | Claude `requestId`; omp `responseId`; Codex `response_id`, else `seg@{ordinal}` | natural / derived |
| tool_call | Claude `tool_use.id`; omp `toolCallId`; Codex `call_id` | natural |

`occ_key` is the same key for leaf kinds (the store keeps its hash, `occ_hash`; a provider request id is found through the `op_id` suffix, D43). **Codex calls without a `response_id`** (builder deviation, T2, 2026-10-12): `occ_key = "fp:" + hash(total_token_usage ‖ last_token_usage)`, not `{thread}:{segment}:{ordinal}`. Reason: the positional form gave a fork's own calls the same key as the parent's later calls, and prefixing the thread id missed 4 real copies; the usage fingerprint is identical on a copied record and differs between genuinely distinct calls. `op_id` for such calls still uses `seg@{ordinal}`; only `occ_key` is the fingerprint.

### 4.4 Call identity, dedup, ownership (D6)

- **Claude Code.** Group assistant records by `requestId`; drop `<synthetic>`; final = last with `stop_reason`, else last with `cut_off = true`; usage from the final record; a zero-usage stub loses to the real record. Fork transcripts: records without `attributionAgent`, and records whose `uuid` already exists in an owner transcript, are copies (F9).
- **Codex.** `token_usage_record` present → one op per `response_id`. Else one op per change of `total_token_usage.total_tokens`, tokens = `last_token_usage`; a decrease starts a new segment and writes a `rollback` event unless the file is a fork/resume copy, in which case the copied prefix is `duplicate_of` the ancestor's ops. Child threads inherit the parent's total as baseline: child usage = Σ its own calls, never its final total. `in_tok = input_tokens − cached_input_tokens`.
- **omp.** One op per assistant message (`responseId`); `model_usage` → `llm_call role = aux` (Q3: included).
- **Ownership.** Unique `(provider, kind, occ_key)` in the store; conflict resolved by the order in invariant 5; the loser gets `duplicate_of` and is excluded from rollups; a later-ingested owner flips ownership and both roots' rollups are recomputed (benchmark I5). The UI reads text through the owner's pointer.

### 4.5 Hierarchy, links, link provenance

- `turn` boundaries: main transcript — a typed user prompt (Claude string content without `<teammate-message`/notification markers; Codex `user_message`/`task_started`; omp non-steering `user`). Agent transcript — an arrival. Notifications and tool results are interior.
- `tool_call.parent` = the emitting `llm_call`; results paired by id only.
- `engagement.parent = caused_by`, with `caused_by_basis`: `id` (Claude `toolUseResult.agentId`/`meta.toolUseId`; Codex `collab_agent_spawn_end.call_id`; omp `results[].id`), `content_hash` (Claude teammates/SendMessage: sha256 of the message body equals the arrival body — 1,840 of 2,874 Claude agents have no id), `name` (omp `tasks[].name`), `time-heuristic` (omp `eval` JS `agent()` spawns: child starts ≤ 2 s after the eval call ends), `none` (no match → parent = session). Basis counts are reported per session.
- **Cycle rule (ping-pong messaging).** Per-message attribution nests each reply under the message that caused it, so two agents exchanging messages would grow depth without bound (adapter-claude hit the depth assert on the full corpus). Rule: let `T` be the target agent of the message. Walk the engagement ancestors of `caused_by`; if any ancestor engagement belongs to `T` (or `T` is the main transcript), the new engagement is **not** parented under `caused_by`; its parent is `T`'s spawn tool call (the parent of `T#0`; for the main transcript, the session), with `caused_by` kept as recorded and `caused_by_basis` suffixed `+cycle` (e.g. `content_hash+cycle`). Effect: a reply attributes to the agent's own spawn rather than to the message; forward messages still attribute per message; depth is bounded by 4k + 3 for k nested agents after the rule (D18). The prototype's REG37 cycle suppression was this rule without the label.
- Cycle (after the rule above) or depth > 32 → the bundle is rejected loudly with the op ids (D18).

### 4.6 Time rules

**Turn span (D38 H1, D48).** A main turn's span is its stored `[start_ms, end_ms]`, not widened over agents it caused (965 of 5,944 such agents end later; the part after `end_ms` is a tail in the Waterfall). Time inside the span but outside the session wall (a resume gap) counts as `span_none_ms`. Window figures (`model_ms` … `none_ms`, DM-51) exist so session sums add up; the UI uses the span figures (DM-53).

**Segments (D19).** A container (`session`, `engagement`, `turn`) carries `segments: [[start_ms, end_ms], …]` for *its own file*, split at `resume` events (omp `session_exit` → resume gaps of up to 27 h in one session). The **store** computes `wall_ms` as the union of segments across the session's and its engagements' files; adapters never compute the cross-file union. Resume gaps count as `idle_ms` and are never part of `active_ms`. A final `session_exit` (disposal) never extends a span.

Own intervals are the `[start_ms, end_ms]` of `llm_call`/`tool_call` ops; container metrics (`wall, active, busy_sum, blocked, idle, max_parallel`) are derived from them and from `segments`: `blocked_ms` = gaps between the own intervals of the container's *own transcript* (`agent_id` equal) during which a descendant engagement has an active own interval; `idle_ms` = the other gaps.

| Field | Claude Code | Codex | omp |
|---|---|---|---|
| `llm_call.start/end/dur` | derived: gap from the previous non-assistant record (includes queue/hook time) | derived: gap between token events (includes tool time; labelled) | present (`timestamp`, `completedAt`, `duration`) |
| `llm_call.ttft_ms` | missing | missing (turn-level `time_to_first_token_ms` on the turn op, present) | present |
| `turn` wall | `turn_duration.durationMs` when present, else span | `task_complete.duration_ms` | span |
| `tool_call.dur_ms` | derived (result record ts) | present (`exec_command_end.duration`) else derived | present |
| `out_tok_per_s` | estimated | estimated | derived from present fields |

### 4.7 Cost rules (D5)

```
reported : provider gives per-call cost (omp)                       → cost_* copied; cost_basis = reported
computed : model (via model_aliases → price_alias) in price_table   → cost_in = in_tok × in; cost_out = out_tok × out;
           cache_read × rate; cache_write = 5m_tok × 5m + 1h_tok × 1h (tiers absent → cache_write_tok × 5m rate)
           cost_basis = computed; price_fingerprint = snapshot hash. Subscription credentials included (API-equivalent, D8).
missing  : tokens present, no catalog entry                          → cost_* = null; cost_basis = missing; excluded from sums;
           ancestors cost_lower_bound = true
```

Price snapshot: models.dev via proj-c `tb/pricing.py`, dated, local; `price_fingerprint` = hash(snapshot + aliases + rule version); a change re-prices `computed`/`missing` ops on the next `botsworth ingest --reprice` without reading source files. Claude `cost-state` totals are stored as `sessions.check_*`; the UI shows `Δ = Σ incl − check`; ops are never adjusted.

### 4.8 Availability matrix

| Metric / event | Claude Code | Codex | omp |
|---|---|---|---|
| tokens in / cache read / out / reasoning | present | present | present |
| cache write 5m / 1h | present (both tiers) | write 0 so far; tiers missing | present; tiers missing |
| per-call cost | computed | computed (5,405 of 21,781 missing: codex variants) | reported |
| session ledger check | present (`cost-state`) | missing | missing |
| LLM call duration | derived | derived (biased by tool time) | present |
| TTFT | missing | per turn | per call |
| tool duration | derived | present (482) / derived (26k) | present |
| exit code | derived (parsed from text) | present | present (partial) |
| compaction | present (trigger, pre/post, dropped, duration) | event only; pre_tokens derived from previous call context | present (before/after) |
| api_error / retry | `isApiErrorMessage`, `error: rate_limit`; retries session-level only | errors in outputs; no retry records | `stopReason`, `errorStatus`, `retryRecovery` |
| limit_state | missing | present on every `token_count` (~44k) | missing |
| limit_hit | derived (parsed hit text: window, reset time) | `rate_limit_reached_type` (none in corpus) | missing |
| skill_invocation | `Skill` tool, `<command-name>`, Read of `…/skills/*/SKILL.md` | `<skill>` context (unverified) | `read skill://…` |
| child link basis | id / content_hash | id | id / name / time-heuristic (15%) |

### 4.9 Source pointers (D11)

```
src://<provider>/<path-relative-to-provider-root>#<locator>[/<json.path>][?h=<hash>]

locator   = L<first>-L<last>            1-based line range (JSONL providers; a single record is L<n>-L<n>)
          | B<start>+<len>              byte range (Claude tool-results/*.txt spills; sub-record ranges)
json.path = dotted path into the parsed record of L<first>, e.g. message.content.0.text   (omit for the whole record)
h         = first 16 hex of sha256 over the addressed bytes (whole lines for L, the range for B); optional, emitted by adapters
```

Examples: `src://claude_code/-Users-dev-projects-x/6d54….jsonl#L1182-L1182/message.content.0.text?h=9b1c…`;
`src://claude_code/-Users-…/6d54…/tool-results/toolu_01AB.txt#B0+618233`;
`src://codex/2026/05/18/rollout-….jsonl#L77-L77/payload.arguments`;
`src://omp/-tmp-proj-b/2026-09-19T15-39-39-103Z_01a0….jsonl#L412-L412/message.content.1.arguments`.

Stored form: `src_files(provider, relpath, prefix)` interned, plus per-row locator and 8-byte hash; `botsworth.store.refs.uri` rebuilds the URI byte for byte (DM-8). Roots per provider and the part each reference resolves (`input`, `output`, `thinking`, `text`): DM-7. Properties: provider-neutral; one formatting/parsing function per side; **stable across re-ingest** (line and byte positions of existing lines never change in append-only files; the path is relative to the provider root so a moved root resolves); **append-tolerant**; **drift-detecting** when `h` is present (the resolver re-hashes and returns `drift: true` with what it found — omp's in-place title rewrite and rotated files); **owner-aware** (duplicates are served through the owner's pointer).

Reconciliation with the prototype's `blob://` decision (2026-08-01): that scheme was already positional (base64 of scope, record index, JSON path) and rejected a *second* ad-hoc `{record}.{block}` format — "one scheme, one resolver", which this keeps. Changes: explicit locator instead of a parser-dependent record index; path relative to the provider root instead of a store-scoped `scope`; optional hash for drift; no 1024-byte inline threshold — the store never stores text, only summaries/snippets.

### 4.10 Steps and the action taxonomy (D13)

```jsonc
{ "rec": "step", "step_id": "<turn_id>#<ordinal>", "turn_id": "<op_id>", "ordinal": 0, "op_id": "<llm_call or tool_call op_id>",
  "tool_name": "<raw provider tool name>|null",
  "verb": "think|say|read|write|edit|run|search|fetch|spawn|skill|ask-user|answer|other",
  "target": "≤200: project-relative path | command head | skill | agent type | URL host | tool name",
  "outcome": "ok|error|exit:<n>|interrupted|unknown",
  "summary": "≤120, deterministic from tool args", "snippet": "≤160 of thinking/text/stdout",
  "start_ms": 0, "dur_ms": 0, "skill": "<attributed skill>|null", "src": "src://..." }
```

v4 adds `steps.skill_load` (the skill name when a step loads a skill; DM-30); `steps.skill` keeps its D15 meaning and stays unset. Steps are built per turn from the content parts of its `llm_call`s and their paired results, in file order; one record can yield several steps and one step can span two records (prototype glossary "Record → Event multiplicity"). `answer` = the turn's final assistant text; earlier text parts are `say`. No LLM; cost never on steps.

| verb | Claude Code | Codex | omp |
|---|---|---|---|
| think | `thinking` block | `reasoning` (summary only; content encrypted) | `thinking` |
| say / answer | `text` block (last one in the turn = answer) | `message` (assistant) | `text` |
| read | `Read`, `NotebookRead` | `exec_command` with `parsed_cmd.type = read`; `view_image` | `read` |
| write / edit | `Write` / `Edit`, `MultiEdit`, `NotebookEdit` | `apply_patch` (one edit step per changed path) | `write` / `edit` |
| run | `Bash` | other `exec_command`; `custom_tool_call exec` (one step per `tools.*` call when parseable, else one) | `bash`, `eval` |
| search | `Grep`, `Glob`, `WebSearch` | `parsed_cmd.type = search`; `web_search` | `grep`, `glob`, `find`, `web_search` |
| fetch | `WebFetch` | MCP fetch tools (by name) | `fetch` |
| spawn | `Agent` | `spawn_agent` (batched: one step per child) | `task` (one per child); `eval` `agent()` |
| skill | `Skill`; slash command (`<command-name>`); Read of `*/skills/*/SKILL.md` | `<skill>` context block | `read skill://…` |
| ask-user | `AskUserQuestion` | `request_user_input` | `ask` |
| other | `SendMessage`, `TodoWrite`, `ToolSearch`, MCP tools (`target` = tool name / server) | `wait_agent`, `collab_*`, `mcp_tool_call`, `update_plan` | `hub`, `wait`, `lsp`, `todo`, … |

Mapping sources: prototype `src/parser/labels.py`, `src/parser/translator.py`, `frontend/src/components/stepDisplay.ts`; Codex `parsed_cmd`.

Rules that keep the taxonomy honest (R-outline):

- Two command fields (D17): `command_norm` = the full normalized command (whitespace/quoting normalized, paths kept); `command_key` = program + subcommand head including launcher prefixes (`uv run pytest`, `make test`, `cargo test`). Repeated-command and slow-test queries group by `command_key`.
- Every `llm_call` and `tool_call` carries `occ_key` from every adapter (the store's cross-file copy detection needs it; Claude `op_id`s embed the session id and cannot serve).
- Every step and `tool_call` keeps the raw provider tool name (`tool_name`) next to the canonical `verb`; the taxonomy is lossy (Grep and Glob are both `search`). Unknown tools map to `other` and keep their name; nothing is dropped.
- A Codex `custom_tool_call exec` script that calls several `tools.*` is **one** `run` step; the inner calls are listed as sub-items of that step (`sub_items: [{tool, target, outcome}]`), never as separate steps.
- `run` labels use the agent's own description when present — Claude Bash `input.description`, omp `toolCall.intent`, Codex `parsed_cmd` — and fall back to a shortened command head only when none exists; the full command stays behind the pointer. Compound commands (`&&`, `;`, `|`, redirects, heredocs, `sed -i`, `tee`) are common and often write files through bash. DEFERRED: a cheap shell parse that splits compound commands into sub-actions and sets a `bash_write` flag when a write is detected.

### 4.10a Naming reconciliation (v2 → canonical)

Builders emit `common.md` names; v2 of this document used others. Canonical = the `common.md` set (it is what the adapters and stores already produce; renaming the design is cheaper than renaming six codebases). Evaluation and the final build use only the left column.

| Canonical (common.md, v3) | v2 name | Note |
|---|---|---|
| `rec = event`, table `events`, field `kind` | `facts`, `fact_kind`, "markers" | one name: event |
| `limit_state` | `limit_snapshot` | |
| `time-heuristic` | `time_heuristic` | hyphen |
| `src://…` | `ptr://…` | grammar in §4.9; `?h=` and `B` range are optional additions |
| provenance `present` | `measured` | |
| step `ordinal`, `summary`, `op_id` | `seq`, `label`, `llm_call`/`tool_call` | one op ref per step |
| `source_file.sha_first_4k` | `tail_hash` | |

### 4.11 Events, accounts, limits, skills (D8, D14, D15, D16)

```jsonc
{ "rec": "event", "event_id": "...", "kind": "compaction|limit_state|limit_hit|api_error|skill_invocation|model_change|resume|rollback",
  "ts_ms": 0, "session_id": "...", "op_id": "<enclosing op>|null", "account": "...", "basis": "present|derived|estimated",
  "fields": { ... }, "src": "src://..." }
```

| kind | fields | basis per provider |
|---|---|---|
| compaction | `trigger (manual\|auto\|unknown), pre_tokens, post_tokens, dropped_tokens?, duration_ms?` | Claude present; omp present; Codex derived (`pre_tokens` = previous call `context_tokens`) |
| limit_state | `account, window_minutes (300\|10080), used_percent, resets_at_ms, plan_type` | Codex present per `token_count`; non-additive series, never summed |
| limit_hit | `window (session\|weekly), resets_at_ms, tz` | Claude derived (parsed from hit text); Codex `rate_limit_reached_type` |
| api_error | `kind (rate_limit\|overloaded\|aborted\|error), status, message_head, retry_ms?, recovered?` | Claude `isApiErrorMessage`/`error`; omp `stopReason/errorStatus/retryRecovery`; Codex output errors |
| skill_invocation | `skill, args_head, via (tool\|slash\|read), skill_md_src, step_id` (the skill step, `turn_id#ordinal`) | Claude, omp present; Codex unverified |
| model_change / resume / rollback | `from, to` / `from_session, copied_ops` / `dropped_tokens` | all (additive kinds beyond common.md's five) |

`account` = `"{family}:{plan or credential}"`: Codex `rate_limits.plan_type` (+ auth); omp `credentialId` → the ChatGPT family for `openai-codex*`; Claude `anthropic:default` until a plan field exists.

**Limits in V1 (D16, the maintainer, narrows D8 and drops D13(a)):** no 5 h / weekly window modelling or estimation and no rolling-window queries. "Limits burning fast" is answered by usage over time — tokens and cost by model, project and session per hour/day/week bucket — and drill-down to the calls and steps that consumed the most. Codex `limit_state`/`limit_hit` stay as raw, searchable events only; `accounts.window_*` columns are not populated. Re-entry: the maintainer asks for window-relative usage.

**Skills in V1 (D15, the maintainer, supersedes D13(b) for V1):** searchable `skill_invocation` events only — which sessions used skill X, when, `via (tool|slash|read)`, and the steps around each invocation (the enclosing turn's steps, addressable by display id or step id). No cost/time attribution to skills in V1: `steps.skill` stays unset and no per-skill cost/time rows are computed. Detection still distinguishes a `Skill` tool call / slash command from a bare SKILL.md read (`via=read`). Attribution (D13(b) rule) is deferred; re-entry: the maintainer asks for per-skill cost.

## 5. Query model

`botsworth.query` (package `src/botsworth/query/`) is the only query layer; HTTP and the CLI wrap it and hold no SQL (QRY-20).
Every function takes `db=` (path or open connection; default `store.location.resolve_db`), opens the store read-only, and checks
the store state first (`NoStore`, `SchemaMismatch`; QRY-22).

| Module | Owns |
|---|---|
| `query/__init__.py` | the facade: error classes (`QueryError`, `NotFound`, `NoStore`, `SchemaMismatch`), `store_state`, the legacy functions below, re-exports of the v4 views and `resolve_project` |
| `service.py`, `shapes.py`, `analytics.py` | the §5 query JSON: execution, validation and `Op`/`Step`/`Event` builders, quantiles and the named analytics (CLI `stats`, `outliers`, `trend`, `limits`, `skills`) |
| `outline.py` | display ids (`T12.3`), id resolution, outline rows (§6.1a) |
| `scope.py` | `Scope`: a period plus filters as one frozen value; `parse_scope` raises `QueryError` naming the field (QRY-3) |
| `wire.py` | the wire types of `interfaces.md` §3.3–§3.9, §5.2 as frozen dataclasses; `to_json` is the one serialiser for HTTP and CLI. The TypeScript mirror is `frontend/src/api/types.ts` |
| `views/explorer.py` | `period_summary`, `day_series`, `list_sessions`, `facets`, `palette`; session membership by day row (QRY-5), full-session figures (QRY-9) |
| `views/session.py`, `stretches.py` | `session_detail`, `session_turns`, `resolve`, model lines; stretch labels and turn attachment (QRY-41) |
| `views/rows.py`, `turn.py` | `CallRow`/`ToolRow`/`AgentRow`/`GapRow` builders; `turn_rows`, `agent_rows`, `histogram` |
| `views/detail.py`, `find.py`, `text.py`, `status.py` | `detail` (call, tool, agent, message); session finders; `src_uri` and lazy `source`; the SQL form of the failure rule (QRY-37) |
| `views/usecases.py` | `project_slow`, `session_breakdown`, `top_tasks` (CLI only, no HTTP route) |
| `views/ingest.py`, `projects.py`, `common.py` | `ingest_state` and the `<db>.lock` reader; `resolve_project` (D49); `lookup_session`, `money`, colour slots |

Legacy functions (design §5, still served; the v4 UI does not call them) plus `sessions`, `projects`, `availability`. The v4 views and their
parameters are listed in `interfaces.md` §2.1. Money is flattened as `cost_usd`, `cost_lower_bound`, `n_unpriced` (QRY-11); a total over
zero sessions is null, never 0.

Signatures of the legacy functions: `query(q)`, `show(op_id)`, `timeline(op_id, depth=3)`, `steps(turn_id, limit=200, cursor=None)`, `source(id_or_src, part=None, offset=0, max_bytes=65536)` (returns `{text, total_bytes, drift, file, locator}`), `outline(scope_id, depth=1)`. The query JSON (`bucket.time` here uses UTC buckets, `service.py`; the v4 views use the request zone `Scope.tz`, D50):

```jsonc
{
  "scope":   {"op": "<op_id>"} | {"sessions": {"provider"?, "project"?, "account"?, "from"?, "to"?}} | {"all": true},
  "select":  "children" | "descendants" | "self" | "sessions" | "events" | "steps",
  "where":   [{"field": "kind", "in": ["llm_call"]}, {"field": "tool_name", "eq": "Bash"},
              {"field": "command_key", "eq": "uv run pytest"}, {"field": "command_norm", "contains": "tests/cost"}, {"field": "is_error", "eq": true},
              {"field": "start_ms", "gte": 1791100000000, "lt": 1791186400000},        // range: gte/gt/lte/lt, any numeric field (drill into one bucket)
              {"field": "event_kind", "eq": "compaction"}, {"field": "skill", "eq": "grill-me"}, {"field": "time_prov", "eq": "present"}],
  "measure": "incl" | "own",
  "metrics": ["cost_usd", "tokens.total", "tokens.cache_read", "wall_ms", "active_ms", "busy_sum_ms", "dur_ms",
              "llm_ms", "tool_ms", "ttft_ms", "out_tok_per_s", "cache_hit_ratio", "context_tokens", "n_errors"],
  "group_by": [] | ["model" | "tool_name" | "agent_type" | "command_key" | "command_norm" | "provider" | "kind" | "file_path" | "session" | "project" | "account" | "skill" | "verb" | "path_sig"],
  "bucket":  null | {"time": "hour" | "day" | "week"},                                                        // D16: no rolling/window buckets in V1
  "agg":     "sum" | "count" | "max" | "p50" | "p90" | "p95" | "distribution",   // distribution = {p50, p90, max, n} per metric
  "outliers": null | {"over": "p90", "with_top_child": true},                   // rows above the scope's quantile, each with its argmax child
  "compare": null | "previous_bucket",
  "order_by": {"metric": "cost_usd" | "<any metric>" | "start_ms" | "n", "dir": "desc"},   // start_ms = chronological; n = group count
  "limit": 10, "cursor": null
}
```

Response (wire contract = `frontend/src/api/types.ts` and `query/wire.py`):

```jsonc
{ "freshness": {"last_ingest_ms", "parser_version", "price_fingerprint"},
  "scope_totals": {"<metric>": number|null},  "scope_prov": {"<metric>": prov}, "cost_lower_bound": bool,
  "rows": [{ "op"?: Op,                       // select children|descendants|self|sessions: the full op record incl. incl_* columns
             "key"?: {"<group_key>": "..."},  // grouped rows
             "bucket"?: epoch_ms,             // bucket start when bucketed
             "event"?: Event, "step"?: Step,  // select events|steps carry the record itself
             "metrics": {"<metric>": number|null}, "prov": {"<metric>": prov},
             "dist"?: {"<metric>": {"p50", "p90", "max", "n"}},     // agg = distribution
             "share": number|null,            // row ÷ scope total for order_by.metric
             "counts"?: {"n", "sessions"?, "first_ms"?, "last_ms"?},  // grouped/bucketed rows; op rows carry n_* on the op
             "delta"?: {...}, "top_child"?: Op|null }],
  "next_cursor": string|null, "availability": {...} }
```

Missing metrics are `null` with `prov: missing`; `out_tok_per_s`/`ttft_ms` carry `estimated` when gap-derived. `cache_hit_ratio = cache_read / context_tokens`. `incl_cost_usd` is served as a single total beside the per-bucket `incl_cost_*_usd` columns. `start_ms` is accepted in `metrics`/`order_by` as a pseudo-metric (returns the op's `start_ms`).

### 5.1 Questions → queries

| Question | Query |
|---|---|
| Why 2M tokens? | scope op S, children, incl, tokens.*, order tokens.total → repeat on the top row → `show` |
| Where did 6 h go? | children incl `[wall, active, busy_sum, blocked, idle, max_parallel]`; descendants own `dur_ms` desc; `timeline(S, 3)` |
| Most expensive last week? | sessions{project, from, to}, descendants kind = session \| engagement \| llm_call, cost desc |
| Tests slow? (D7d, Q16) | sessions{project}, descendants tool_call Bash, group_by command_key (D17; `command_norm` keeps the full normalized text for drill-down), agg distribution of dur_ms |
| Per-session distributions (D7a/b, Q11) | sessions{project, MONTH}, select sessions, agg distribution of `[cost, tokens.*, wall, llm_ms, tool_ms]`; group_by `[agent_type]` / `[model]` / `[tool_name]` |
| Outliers (D7c, Q12) | same scope, select sessions (or descendants kind = engagement), `outliers {over: p90, with_top_child: true}` |
| Getting slower? (R-trend, Q13) | all, descendants llm_call, group_by model, bucket day, metrics `[dur_ms p50, out_tok_per_s p50, ttft_ms p50]`; `where time_prov = present` for the honest series |
| Usage over time / "limits burning fast" (D16, Q14) | all or sessions{project}, descendants llm_call, bucket day, group_by [model] (or [project], [session]), metrics tokens.total, cost_usd, order desc; then children/descendants of the top bucket's sessions by tokens.total → `steps(turn_id)`. Raw `limit_state`/`limit_hit` events searchable via select events. |
| Skill used? (R-skill, Q15, Q19; D15: events only) | select events kind = skill_invocation, where skill = X, group_by session \| skill, bucket week; for each event, `steps(turn_id)` of the enclosing turn (first 20) with outcomes. No per-skill cost/time in V1. |
| Compactions? (Q17) | select events kind = compaction group_by session, fields.pre_tokens; `show` of the enclosing turn for the next calls |
| What did the agent do in this turn? (R-steps, Q18) | `steps(turn_id)` |

### 5.2 Secondary functions (shapes = `frontend/src/api/types.ts`)

- **show(op_id)** → `{op, ancestors: Op[], children_counts: {kind: n}, events: Event[]}`.
- **timeline(op_id, depth)** → `{root: Op, ops: Op[], events: Event[]}`; `ops` = every op in the subtree down to `depth` levels of *engagement nesting* (not tree levels; one agent = 4 tree levels), root excluded; container ops carry `segments` (D19) and `wall/active/busy_sum/blocked/idle_ms`, so lanes and gaps render from op fields alone.
- **steps(turn_id, limit, cursor)** → `{turn: Op, steps: Step[], next_cursor}`; `step_id = "{turn_id}#{ordinal}"` (stable: ordinals are contiguous per turn).
- **source(id_or_src, part, offset, max_bytes)** → `{text, total_bytes, drift, file, locator}`; never stored.
- **outline(scope_id, depth)** → `{rows: [{display_id, id, kind|verb, target, outcome, dur_ms, cost_usd?}]}` (§6.1a).

## 6. API surface

Spec: `interfaces.md` §3 (HTTP, API-n), §5 (CLI, CLI-n), §2 (query functions). CLI JSON equals the matching HTTP body (CLI-3); within `api_version` 1
fields are only added (CLI-11).

### 6.1 Python API (agents, scripts)

`import botsworth` → `botsworth.query(...)`, `show`, `timeline`, `steps`, `source`, `outline`, plus the v4 views (§5). Pure reads over the store; no refresh side
effects; every result carries `freshness` (`last_ingest_ms`, `parser_version`, `price_fingerprint`, `tz`, `schema_version`, `api_version`).

### 6.1a Outline view and display ids (R-outline)

The agent's default read of a turn or session is the **outline**: one line per step — display id, verb, target, outcome, duration — with cost on `answer` and turn lines, and no content:

```
T12.2  run     pytest -q tests/cost        → exit:1   4.2s
T12.3  edit    src/cost.py                 → ok       0.1s
T12    answer  "Fixed the rounding…"        → ok       41s   $0.38
```

Display ids (`T12`, `T12.3`, `T12.3/2.1` inside a spawned agent) are positional shorthand computed at render time; every CLI/API call accepts them **and** the stable `op_id`/`step_id`, and every outline row carries both. Content is on demand only: `show <id>` returns metadata, provenance and pointers; `source <id> --part input|output|thinking|text --max-bytes N [--offset]` pages the text through the pointer. `outline(scope_id, depth=1)` is the sixth function of the Python API, with the same `--json` shape as the CLI.

### 6.2 CLI (agents)

One entry point `botsworth` (D35). Global `--db PATH` (precedence: flag > `configure()` > env `BOTSWORTH_DB` > env `BOTSWORTH_HOME` (a folder) > the platform app-data folder (`platformdirs.user_data_dir("botsworth")`: macOS `~/Library/Application Support/botsworth`, Linux `~/.local/share/botsworth`, Windows `%LOCALAPPDATA%\botsworth`) holding `botsworth.sqlite`, D20). Output is one line of JSON;
`--table` prints a table of at most 2 KB. Argument parsing only; all logic in `botsworth.query`.

| Group | Commands |
|---|---|
| Data management | `ingest [--provider P]… [--force] [--reprice] [--rebuild]`; `serve [--host] [--port 8420] [--ui DIR] [--open]`; `status` |
| Explorer | `period`, `days`, `sessions`, `facets`, `search TEXT` |
| Drill-down | `session`, `turns`, `turn`, `detail`, `find` |
| Use cases (CLI-20..22) | `slow` (project time split, slowest tools, waits, vs the previous period), `breakdown SESSION` (turns, rows, agents, waits by time/cost/tokens), `tasks --by command_key\|tool\|verb\|skill\|agent_type\|slash [--measure tokens\|cost\|time\|count]` |
| Legacy (kept) | `outline`, `query`, `top`, `children`, `show`, `steps`, `timeline`, `source`, `stats`, `outliers`, `trend`, `limits`, `skills` |

- Scope flags (CLI-5), shared: `--from/--to DAY` or `--since <n>d\|w` (request zone: `--tz`, else the machine zone, today included; D50), `--tz`, `--q`, `--provider`, `--model`, `--branch`, `--project`, `--automated`.
- **Project resolution (D49).** Each `--project` value resolves to a project key: an exact key, else an exact `projects.name` (ASCII case-insensitive), else a unique key suffix
  on a `/` boundary. Several matches or none is exit 1 with up to 5 candidate keys and names (`views/projects.py`). Other filters (`--model`, `--branch`) still return an empty result silently.
- Exit codes (CLI-4): 0 success; 1 rejected request (bad value, unknown or ambiguous id, missing source, ingest running); 2 usage error, no store, schema mismatch; 3 `serve` could not bind; 130 interrupted.
- `slow`, `breakdown`, `tasks` end their `--help` with the definitions of their output fields (`time_split`, `time_prov`, `user_waits`, `waits`, `basis`, `unassigned`, `before_first`).

### 6.3 HTTP (UI)

`botsworth serve` (stdlib `ThreadingHTTPServer`, `http/server.py`). One route table `ROUTES`; the handler calls the named view and serialises with `wire.to_json`. Wire contract: `frontend/src/api/types.ts`.
Errors are JSON `{"error", "code"}`, never HTML. Data responses carry `Cache-Control: no-cache` and a weak ETag over (api, schema, last ingest, price fingerprint, tz); a matching `If-None-Match` gets 304
(API-6). Responses over 32 KB are gzipped. Non-`/api` paths serve the built UI (`--ui`, env `BOTSWORTH_UI_DIR`, bundled `botsworth/http/ui/`, or `frontend/dist`).

| Route (GET unless noted) | View |
|---|---|
| `/api/ingest/status` (always 200) | `ingest_state` |
| `/api/explorer/summary`, `/days`, `/sessions`, `/facets`; `/api/palette` | `period_summary`, `day_series`, `list_sessions`, `facets`, `palette` |
| `/api/sessions/{s}`, `/turns`, `/find`, `/histogram`, `/resolve` | `session_detail`, `session_turns`, `find`, `histogram`, `resolve` |
| `/api/turns/{t}/rows`, `/histogram`; `/api/ops/{e}/rows` | `turn_rows`, `histogram`, `agent_rows` |
| `/api/ops/{op}/detail`; `/api/source` | `detail`; `source` (lazy full text, `no-store`) |
| `POST /api/query`; `/api/ops/{op}`, `/timeline`, `/outline`; `/api/turns/{t}/steps`; `/api/sessions`, `/api/projects`, `/api/availability` | legacy views for agents and scripts |

UI (`frontend/src/`): `blades/{explorer,session,turn,detail}`, `shell/` (blade strip, breadcrumb, keys, freshness), `ui/` (shared patterns), `state/` (URL and period), `api/` (client and types). Stack: React 19, TypeScript, Tailwind 4, Vite; checks are `npm run build`, `npx vitest run`, `npx oxlint`.

### 6.4 Adapter contract

`the canonical-stream contract (not in this repo; summarised in design §6.4 and `src/botsworth/stream.py`)` §2 is authoritative: adapters parse provider files and emit the canonical stream (`op`, `step`, `event`, `source_file` records) per bundle; they are pure (files in, records out), apply §4.4–§4.6, §4.10–§4.11 and the pricer, and never compute inclusive numbers or cross-bundle ownership. `source_file` = `{path, size, mtime, sha_first_4k}` plus `ts_list` (v4, DM-40: record timestamps for `max_silence_ms`; stream only, never stored). Packages: `src/botsworth/adapters/{claude,codex,omp,common}`.

## 7. Data — persisted vs derived

Truth: provider files. The store is a disposable cache over them (D52): no canonical streams are kept on disk, and ingest writes nothing but the store. Editable data: price snapshot, aliases. Everything else is derived and rebuildable
(`botsworth ingest --rebuild`, or a schema change). A rebuild builds `<db>.rebuild` beside the store and renames it over the store (§8). When a provider log is deleted, its rows stay (`source_files.status = source_missing`) until a rebuild drops them; D52 accepts that loss.

**Stored (schema v7, `data-model.md` §2.2–2.3, §4; `store/schema.py` CONTRACT).** Tables: `ops`, `steps`, `events`, `sessions`, `rollup_time`, `stretches`, `projects`, `src_files`, `names`, `source_files` (the change manifest; `reject_reason` marks a bundle the store refused), `price_table`, `model_aliases`, `accounts`, `meta`; views `ops_v`, `steps_v`, `events_v`.

- `ops`: the tree with nested-set `pre`/`post` (descendants = `root_rowid` and `pre BETWEEN`; DM-15); compact content reference (`src_file`, `src_loc`, `src_h`); `occ_hash`; interned `cwd`, `account`, `path_sig`; v4 columns `turn_kind`, `turn_origin`, `display_ix`, `attr_turn_rowid` and `attr_*` sums (per-turn cost including caused agents, DM-45), `span_wall_ms` (DM-53; the session, window and span splits are rows of `time_split`, DM-51, D54), `max_silence_ms`, `user_wait` (DM-40).
- `sessions`: title (`title_source`, `display_title`), `n_human_asks`, `is_automated`, activity bins (`activity_bin_ms`, `activity_tok`, ≤ 96 bins), time split, `entrypoint`, `originator`, session-bounds covering every owned leaf (DM-62).
- `rollup_time`: one bucket size, 15 min aligned to the epoch (`bucket_start_ms = start_ms − start_ms % 900000`), speed and TTFT sums (DM-41 [D50], DM-47). The store holds no zone and no local-day text (D50: all stored times are epoch ms). A local day, week or hour is an epoch range computed at query time in the request zone (`query/zone.py`); every real zone offset is a multiple of 15 min, so its edges fall on bucket edges. A session is in a period when it has buckets with `n_llm_calls > 0` inside the period's epoch range (DM-54).
- `stretches`: every no-activity (`none`) or agents-only (`agents`) interval of at least 60 s, cut at main-turn starts (DM-52). `projects`: friendly names and counts (DM-60, DM-61).
- `meta`: `schema_version`, `parser_version`, `price_fingerprint`, `last_ingest_ms`, `tz`, `last_run`, `n_ownership_disagreements`.

**Read through references, never stored (DM-13):** prompts and asks beyond the label, thinking, model text, tool input and output, agent prompts, file contents, full commands, per-record timestamps. `source()`
resolves a `src://` URI against the provider root (`pointer.DEFAULT_ROOTS`) and returns `drift: true` when the hash no longer matches.

## 8. Ingestion (D7, D15, D37, D43)

Owner: `pipeline.run` (`pipeline.py` CONTRACT) → `store.ingest.ingest` → `store.load.load_bundle`. `PARSER_VERSION = "2"`, `SCHEMA_VERSION = 7`.

1. `botsworth ingest` only (by hand or from a hook); queries never trigger it; responses carry `freshness`. The run holds `<db>.lock` (`{pid, started_ms}`); a second run raises `IngestLocked`. Readers use the pid to show "Ingest running" (QRY-40).
2. **Bundles.** Each adapter discovers files and groups them into bundles (one session: main transcript plus subagents). A bundle is rebuilt when `--force`, or a file is new, gone, or its `(size, mtime_ns, sha_first_4k)` differs from `source_files`, the only change manifest. The adapter's `Bundle` goes from memory to the loader, one bundle at a time; nothing is written beside the store (D52). A bundle that an adapter or the store rejects gets `reject_reason` on its `source_files` rows and is not tried again until one of its files changes. `--rebuild` ignores the manifest: it fills `<db>.rebuild` from the logs and `os.replace`s it over the store; on failure the old store stays and the temp file is removed.
3. **No tail resume.** A changed bundle is re-read from whole files; the v3 plan to resume from `bytes_ingested` was not built (`pipeline.py` KNOWN LIMITATIONS). A partial trailing line is skipped and the file is `live`; a file is also `live` when its mtime is within 10 min of the run start (D26).
4. **Fold** (per bundle, one transaction, previous rows kept if it fails): validate the stream and the tree (one root, no cycle, depth ≤ 32, D18); decide ownership against the whole store and refold other bundles whose owned set changed (§4.4); inclusive rollups and time (`intervals`); `pre`/`post`; `display_ix`; attribution walk (DM-45, D46); titles; activity bins; `rollup_time` 15-min epoch buckets; time split and stretches (`timesplit`, DM-50–53, D48); `steps.skill_load`. Bundles load oldest first: the pipeline sorts refs by the first timestamp of each main file (`adapters.session_start_ms`, a peek of at most 200 lines; no timestamp sorts last; ties by provider and bundle id), then builds and loads one at a time. After the loads: `projects.refresh`, Codex thread-name overrides (DM-12), `meta.last_run`.
5. **Time zone** (D50): ingest uses none. The zone is a query parameter (`Scope.tz`; HTTP `tz`, CLI `--tz`, the UI sends the browser zone); absent, the answering machine's zone (`query/zone.resolve_tz`: `BOTSWORTH_TZ`, `TZ`, `/etc/localtime`, else UTC). Changing zones never touches the store. CLI JSON adds ISO-with-offset siblings for timestamps, `_ms_h` readable durations and a top-level `tz` (Q31, D50-T4).
6. `--reprice`: re-cost `llm_call` rows whose `price_fingerprint` differs; no file reads. `ANALYZE` runs after any run that changed a bundle (DM-5).
7. **Rebuild by swap (DM-4, D37).** If the store is missing or its `schema_version` differs, the run builds `<db>.rebuild`, then `ANALYZE`, `VACUUM`, `journal_mode=DELETE`, `os.replace` onto `<db>`. Any failure discards `<db>.rebuild*` and leaves `<db>` untouched, so the UI never reads dropped tables. `--force` on a current store rebuilds every bundle in place; it is not a swap. A parser change alone does not rebuild bundles (DM-90): use `--force`.
8. **PERF note (gates/PERF.md).** `ownership.owned_keys` runs once per bundle. It joined `ops` to `sessions` by `bundle`; a store under rebuild has no `sqlite_stat1` until the closing `ANALYZE`, and the planner scanned every leaf op (3.07 s per call at 581k ops; 429 s of 581 s in cProfile). It now reads `FROM ops INDEXED BY ops_root_kind WHERE root_rowid IN (SELECT rowid FROM sessions WHERE bundle=?)` (0.03 s). Full-rebuild load time 591 s → 107 s; every stored row identical except `source_files.ingested_at_ms`. Two other bundle-scoped statements (`_root_rowid`, `_delete`) still filter `sessions` by `bundle` with no index (2.9 s of 581 s).

## 9. Failure handling

| Dependency | Failure | System does | User sees |
|---|---|---|---|
| Provider file | partial/malformed line | skip, count | `n_malformed`; status `live` |
| Provider file | unknown record type | ignore, log once per bundle | ingest status |
| Provider file | deleted | keep ops, `source_missing` | detail says missing; `source` → 404 |
| Pointer | hash mismatch | return text + `drift: true` | drift marker |
| Linking | no spawn match | parent = session, `none` | flagged engagement |
| Linking | cycle / depth > 32 | bundle rejected | ingest error with ids; old rows stay |
| Price snapshot | fetch fails | keep last snapshot, report date | freshness shows snapshot date |
| Price table | unknown model | `missing`, lower bound | `≥` marker on ancestors |
| Store | write interrupted | transaction rollback | previous bundle remains |
| Ownership | later owner arrives | both roots recomputed | logged in ingest status |
| Store | no file | 503 `no_store`; CLI exit 2; `/api/ingest/status` still 200 with `store_exists: false` (D22) | empty-state banner |
| Store | `schema_version` differs (a v6 store) | 503 `schema_mismatch`; CLI exit 2 naming `botsworth ingest` | "Store needs a rebuild." (SYS-57) |
| Store | schema change | rebuild into `<db>.rebuild`, swap on success; on failure `<db>` untouched | — |
| Store | ingest running | `<db>.lock` holds a live pid; data routes send `no-store` | "Ingest running — figures may be partial"; no automatic refetch (D38 H3) |
| Query | bad argument, unknown or ambiguous id | `QueryError` (400, CLI exit 1) / `NotFound` (404) | JSON `{"error", "code"}` |
| CLI | `--project` matches none or several | exit 1, up to 5 candidates (D49) | message |
| Content | source file gone | `SourceMissing` (404) | detail says missing |

**Fallback rule (project conventions, adopted by D37).** Failure handling in any layer (`except`, `.get()` with a default, `or ""`, return-None-on-failure,
log-and-continue) either asserts or carries a `# FALLBACK-OK: <spec id or file>` comment (`// FALLBACK-OK:` in TypeScript) that names the source enumerating the state.
An uncited fallback is a review finding. Layer rules: adapters are pure; the store does not read provider files; the query layer does not write; `cli/` and `http/` hold no SQL.

## 10. Contracts

**Adapter** — GUARANTEES: §4.4 identity, §4.5 links with basis, §4.6 times with provenance, §4.10 steps, §4.11 events, a `src` on every record; pure. DOES NOT: rollups, ownership, storage.
**Pricer** — GUARANTEES: §4.7; aliases only; never guesses; stamps `price_alias`, `price_fingerprint`.
**Store/engine** — GUARANTEES: invariants 1–6, 8; atomic bundle replace; deterministic ownership; rollups consistent after every ingest; accurate `freshness`. DOES NOT: read provider files; render.
**Query layer** — GUARANTEES: §5 semantics; `share` ≤ 1 over a complete children list; nulls for missing; `prov` on every row. DOES NOT: refresh; read source files except `source()`.
**Resolver** — GUARANTEES: §4.9; drift detected when `h` present; owner's pointer served for duplicates.

The per-file CONTRACT docstrings under `src/botsworth/` are the current, tested form of these contracts; this section is the summary.

## 11. Testing

- **Gate.** `make check` = `ruff check` + `ruff format --check` + `pytest tests/unit` (1057 passed, 7 skipped at the CLI-UX landing). Frontend, from `frontend/`: `npm run build`, `npx vitest run` (962 tests at the FX landing), `npx oxlint`. Run `uv run python -m pytest` inside a workspace; bare `pytest` imports the main checkout.
- **Fixture store (`qstore`).** `tests/unit/qstore.py` builds a schema-9 store once per test session from the synthetic stream files in `tests/fixtures/qstore/streams`, read by the test helper `tests/unit/stream_load.py` and passed to `store.ingest.ingest` (product code reads no streams folder; run with `BOTSWORTH_TZ=Asia/Kolkata`; TST-1). Tests treat it as read-only; the real store is never opened. Other builders: `explorer_store.py`, `q2c_store.py`, `store_builders.py`. Fixtures are synthetic.
- **Parity tests.** CLI JSON equals the HTTP body per command (`test_cli_v4.py::test_parity`, TST-2). `find` output equals the pre-optimisation output (`test_find_parity.py` against `tests/unit/data/find_parity.json`, captured from the unchanged code). The SQL and Python forms of the failure rule agree (`test_detail_view.py::test_failed_sql_equals_tool_status`). Each view's CONTRACT line has a test (GUARANTEES, EXPECTS violations, FAILURE BEHAVIOR).
- **Cost invariants (TST-3).** Σ turn `attr_cost_usd` + unattributed = `sessions.incl_cost_usd`; time-split states sum to `wall_ms` (DM-51, DM-53); checked on the fixture and on the real store (`gates/B4.md`, `gates/A1.md`).
- **Latency (TST-5).** `make bench-api [DB=path]` times every API-50 endpoint over HTTP and the three CLI-24 commands on the real store (default `~/.old-profiler/v8/profiler.sqlite`), read-only, 3 warm-up and 20 timed runs; not part of `make check`.
- **Other targets.** `make acceptance` (UC1–UC16 on the frozen corpus, `tests/acceptance`), `make bench` (Q1–Q19 thresholds, `tests/bench/bench.py`); `tests/unit/test_store_corpus.py` runs only when `BOTSWORTH_STREAM_DIRS` / `BOTSWORTH_CORPUS_DB` are set.
- **Legacy.** Fixtures F1–F11 (`approaches.md`, amended by `common.md`) cover invariants 2–6, 8, time rules, ownership order, steps order, skill and compaction events (`tests/fixtures/F1..F9`).

## 11a. Measured (2026-10-13)

**Current store (schema 8, after D50; `evidence/A3-size-latency.md`).** `~/.old-profiler/v8/profiler.sqlite`, rebuilt from 1,639 bundles (0 rejected, 325 s): 739,188,736 bytes (46.9 % of v6), 583,620 ops, 1,266.6 bytes per op, `rollup_time` 23,651 rows. Every API-50 and CLI-24 case is within target (`make bench-api` exit 0; worst: `find search` p95 147 ms of 300, `cli tasks 30d` 882 ms of 2,000). The figures below are the history that led there (schema 7).

Sources: `gates/B4.md`, `PLAN.md` run log, `gates/PERF.md`, `evidence/q4-latency.md`, `gates/FIND-PERF.md`, `gates/A1.md`. Corpus: 1,636–1,637 bundles (Claude Code 1,116, Codex 423, omp 97).

**Store size.** B4 run 1 (full ingest of the then-current corpus, 0 of 1,636 bundles rejected): 744,988,672 bytes, 581,261 ops, 1,281.67 bytes per op. Targets (DM-16): ≤ 787,466,240 bytes and ≤ 1,390 bytes per op, from the v6 store
at 1,574,932,480 bytes, 566,761 ops, 2,779 bytes per op. After the D48 and PERF changes, the `--force` rebuild (1,637 bundles, 0 rejected, 238 s) gave 772,911,104 bytes, 582,057 ops, 1,327.9 bytes per op (in-place reload, not VACUUMed; both targets met). Largest objects in B4: `ops` 366 MB, `steps` 111 MB, `ops_op_id` 58 MB.

**Ingest time.** B4 run 1: 2,317 s wall, 1.02 GiB peak RSS, while other jobs ran (mostly load, per PERF). An unchanged re-run: 14.5 s (5 live bundles rebuilt). After `owned_keys` was fixed: 107 s for the load phase (591 s before), 238 s for the `--force` run through `pipeline`.

**Latency** (p95 ms, warm, v7 store, largest session `b2c3d4e6` where a session is needed; `q4-latency.md` before FIND-PERF, `FIND-PERF.md` after). 26 of 30 cases passed in Q4; the four failures were all `find`.

| Case | Target | p95 |
|---|---|---|
| `explorer/summary` 7 d | 150 | 19.4 |
| `explorer/sessions` all, 200 rows | 200 | 28.6 |
| `turns/{t}/rows`, largest turn | 200 | 51.1 |
| `sessions/{s}/histogram`, largest | 400 | 59.4 |
| `find failures` / `skills` / `agents` / `search` | 300 | 381 / 420 / 442 / 497 before; 62 / 52 / 38 / 159 after |
| CLI `slow` 7 d, largest project | 1,500 | 321 |
| CLI `breakdown`, largest session | 1,500 | 867 |
| CLI `tasks --by command_key`, 30 d | 2,000 | 1,197 |

The session page payload for 200 rows is 176 KB (about 40 KB gzipped) against the 150 KB estimate in API-52; accepted.

**E2 (agent evaluation).** A fresh agent given only the CLI answered the project and session questions; every figure it quoted equals the independent SQL truth, and TST-3 items 1–6 pass on the real store (largest difference 1.4e-13). Usability findings led to D49.

**Day totals.** `period --from D --to D` returns whole-session totals for sessions in the period (QRY-9), not the day's calls. For 2026-10-07 (IST, Claude Code) the day rollup gives 1,847,233,004 tokens and $549.89 (equal to v6); the period summary gives $750.80 because four sessions cross midnight (`evidence/oct7-investigation.md`).

## 12. Pressure test

| Change | Contained in |
|---|---|
| Provider renames a field | one adapter |
| New event kind | adapter + §4.11 row; store unchanged (`fields` JSON) |
| New provider | one adapter; a link-basis row |
| Price snapshot update | `--reprice`, no file reads |
| Drop the UI | CLI/Python unaffected |
| Taxonomy grows a verb | adapter + mapping table; store unchanged |
| Tool-call token attribution (Q4) | own `estimated` metric on the leaf; invariant 2 holds |

Awkward points, accepted: ownership flips make incremental ingest non-local (two roots; I5 measures it); Codex old-format call ids are derived and copies are detected by segment, not id — provenance says so.

## 13. Decided / spikes

Decided: §4–§9 and D1–D49. Spike reports (`spike-sqlite-rollup.md`, `spike-duckdb-spans.md`, `spike-memidx-log.md`) fed the engine choice; the build uses SQLite (`store/schema.py`).
Open items for the maintainer: none. Deferred work: §3.

## 14. Rejected candidates

`approaches.md` § Dismissed.
