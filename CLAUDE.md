# Botsworth

Reads Claude Code, Codex and omp session logs, folds them into a local SQLite store (tokens, cost, time per
session/turn/call), and serves a React UI, an HTTP API and a CLI over that store. Answers: where did time, tokens and
money go. Package `src/botsworth`, CLI `botsworth`. Users: see `README.md`.

## Commands

All go through the Makefile (`make help`). Python only via `uv run`, never bare `python3`.

- `make setup`: `uv sync` + `npm ci` in `frontend/`. `make check`: the gate. Ruff, pytest (`tests/unit`), vitest, `tsc -b` +
  vite build, oxlint. Run it before every commit. `make test`, `make lint`, `make fmt` run the parts.
- `make ui` builds `frontend/dist`. `make build` = ui + `uv build`; the wheel bundles the UI at `botsworth/http/ui/`.
- `make run`, `make ingest`, `make paths`, `make bench-api`: take `DB=path`; empty = platform default store.
- `make clean` removes build outputs only, never a store.

## Project map

Data flows one way: provider files -> `adapters` -> bundles in memory -> `store` -> `query` -> `http` / `cli`.

| Path | What |
|---|---|
| `src/botsworth/adapters/{claude,codex,omp,common}` | provider files -> `Bundle` (`op`, `step`, `event`, `source_file` records) |
| `src/botsworth/pipeline.py`, `stream.py` | ingest entry point (`botsworth ingest`); `stream.py` = bundle checks, plus stream files for tests and the adapter dev command |
| `src/botsworth/store/` | SQLite schema (version 9), ingest/load, derive, rollups, ownership, `location.py` (data folder) |
| `src/botsworth/query/` | the only layer that reads the store: `views/`, `wire.py` types, `zone.py` |
| `src/botsworth/pricing/` | bundled models.dev snapshot + alias table; `snapshot.fetch` is the one network call, by hand only |
| `src/botsworth/http/`, `cli/` | stdlib server and argparse CLI; both call `query` only |
| `src/botsworth/pointer.py` | `src://` pointers to source text, resolved on demand |
| `frontend/src/` | React 19, TypeScript, Tailwind 4: `api/` (typed client), `shell/`, `blades/`, `lib/`, `theme.css` |
| `tests/unit`, `tests/fixtures` | pytest suite and synthetic fixtures (`qstore/` builds the shared query store) |
| `docs/specs`, `docs/design` | interfaces.md and data-model.md (specs), design.md and decisions.md (design, decision log D1..) |

`tests/bench/bench_api.py` runs against a store you pass with `--db`; `make check` does not run it.

## Conventions that bite

- Failure handling in any layer (`except`, `.get()` with a default, `or ""`, return-None-on-failure, log-and-continue)
  must assert, or carry a comment naming the spec/design id that lists the state: `# FALLBACK-OK: <id>` in Python,
  `// FALLBACK-OK: <id>` in TypeScript. An uncited fallback is a review finding.
- Stored times are epoch ms only (D50). No stored zone; the zone is a request parameter (`Scope.tz`, `--tz`, browser).
  Days, weeks and hours are epoch ranges computed at query time in `query/zone.py`.
- Cost and usage count once per requestId group, never per record.
- Provider log folders (`~/.claude`, `~/.codex`, `~/.omp`) are read-only. The store holds no prompt, output, thinking or
  tool I/O text (D39); long text is reached through `src://` pointers.
- No SQL or aggregation in `http/` or `cli/`. Output carries no generated prose.
- The store is the only thing ingest writes (D52): no `streams/` folder, no temp files. It is disposable: a schema version mismatch rebuilds it, and so does `botsworth ingest --rebuild` (new file beside it, renamed over). Never touch the user's real store from tests or tools;
  pass `--db <temp>` or set `BOTSWORTH_HOME` to a temp dir. Override order: `--db`, `BOTSWORTH_DB`, `BOTSWORTH_HOME`, default.
- Source modules start with a CONTRACT docstring (GUARANTEES, EXPECTS, FAILURE BEHAVIOR, DOES NOT). Change it with the code.
- Frontend: colours only from `theme.css` tokens, no raw hex or `rgb()` in components. Money uses tabular numerals.
  All data comes through `frontend/src/api/client.ts`; the wire types follow `docs/specs/interfaces.md`.
- Specs in `docs/specs/` are the source of truth. If code and spec disagree, stop and raise it; do not edit the spec to fit.

## Tests

- Python: pytest in `tests/unit`. Build stores from `tests/fixtures/` with the real pipeline (`tests/unit/qstore.py`
  fixtures `qstore`, `qroots`); do not hand-write SQLite rows. Fixtures are synthetic; never copy real session text in.
- Every GUARANTEE, EXPECTS violation and FAILURE BEHAVIOR of a changed component gets a test. A new feature needs a test
  that runs the new path; do not add tests only to raise the count.
- Frontend: vitest next to the file (`*.test.ts[x]`). `make check` runs all of it.

## Commits

- Work on a branch, not `main`. Stage by path (`git add <paths>`); never `git add -A`, `git add .` or `git commit -a`
  (a working tree can carry untracked local files). Check `git status --short` before each commit.
- Style: a short imperative summary of one outcome, with the task id in brackets when there is one (`... [BW2]`).
- Do not push or publish (no PyPI upload) unless asked.
