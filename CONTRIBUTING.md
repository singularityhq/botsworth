# Contributing to Botsworth

Botsworth reads Claude Code, Codex and omp session logs into a local SQLite store and serves a web UI, an HTTP API
and a CLI over it. This file tells you how to build it, test it and send a change.

## Setup

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node (the version in `frontend/.node-version`).

```sh
git clone https://github.com/singularityhq/botsworth && cd botsworth
make setup     # npm ci and UI build in frontend/, then uv sync
make check     # the gate: ruff, pytest, vitest, tsc + vite build, oxlint
```

`make help` lists the other targets (`make test`, `make lint`, `make fmt`, `make ui`, `make build`, `make run`).
Run Python only through `uv run`. Run `make check` before every commit; CI runs the same command.

## Where things are

`CLAUDE.md` has the project map: which folder does what, and the order data flows in (provider files, adapters,
store, query, http/cli). The specs are in `docs/specs/` (`interfaces.md` for the API and CLI, `data-model.md` for the
store). The design notes and the decision log (D1, D2, ...) are in `docs/design/`.

## Rules that are easy to break

- **Specs are the source of truth.** If code and spec disagree, raise it in the issue or pull request. Do not edit the
  spec to fit the code. If you change behaviour, change the spec in the same pull request.
- **No silent fallbacks.** A fallback is an `except`, a `.get()` with a default, `or ""`, a return of `None` on
  failure, or log-and-continue. Each one must either assert, or carry a comment that names the spec or design id
  that lists the state: `# FALLBACK-OK: <id>` in Python, `// FALLBACK-OK: <id>` in TypeScript. A fallback with no
  such comment is a review finding.
- **Times are epoch milliseconds.** The store keeps no time zone. The zone is a request parameter (`Scope.tz`,
  `--tz`, the browser). Days, weeks and hours are computed at query time in `src/botsworth/query/zone.py`.
- **Cost and usage count once per requestId group**, never once per log record.
- **The store holds no prompt, model output, thinking or tool input and output text.** Long text is read from the
  original log on demand through `src://` pointers (`src/botsworth/pointer.py`).
- **Provider log folders are read-only.** Never write to `~/.claude`, `~/.codex` or `~/.omp`.
- **Layers.** `http/` and `cli/` call `query/` only. They contain no SQL and no aggregation, and their output has no
  generated prose.
- **Never touch your real store from tests or tools.** Pass `--db <temp file>` or set `BOTSWORTH_HOME` to a
  temporary folder.
- Each source module starts with a CONTRACT docstring (GUARANTEES, EXPECTS, FAILURE BEHAVIOR, DOES NOT). Update it
  with the code.
- Frontend: colours come from `theme.css` tokens only (no raw hex or `rgb()` in components), money uses tabular
  numerals, and all data goes through `frontend/src/api/client.ts`.

## Tests

- Python tests are in `tests/unit` (pytest). Build stores from `tests/fixtures/` with the real pipeline, using the
  `qstore` and `qroots` fixtures in `tests/unit/qstore.py`. Do not write SQLite rows by hand.
- Fixtures are synthetic. Never copy real session text, prompts or paths from your own logs into the repository.
- Every guarantee, expectation and failure behaviour listed in the CONTRACT of a component you change needs a test.
  A new feature needs a test that runs the new path.
- Frontend tests are vitest files next to the source (`*.test.ts[x]`).

## Commits and pull requests

- Work on a branch, not `main`.
- Commit message: a short imperative summary of one outcome, for example `Show stalled as its own segment`.
  Put the task id in brackets at the end when there is one.
- Stage files by path. Do not use `git add -A` or `git commit -a`; a working tree can hold local files you do not
  want to publish.
- Open a pull request with `make check` passing. The pull request template lists what we look at.

## Adding a provider adapter

An adapter turns one provider's session files into a `Bundle` of canonical records (`op`, `step`, `event`,
`source_file`). The store, query layer, CLI and UI do not change for a new provider, except for the items below.

1. Create `src/botsworth/adapters/<package>/` with an `adapter.py` that defines a module-level `ADAPTER`. It must
   follow the `Adapter` protocol in `src/botsworth/adapters/__init__.py`: `provider`, `discover(root)` (returns
   `BundleRef`s) and `build(ref, pricer)` (returns a `Bundle`; pure, so files in and records out). Use
   `src/botsworth/adapters/claude`, `codex` and `omp` as models, and `adapters/common` for shared helpers.
2. Register the package in `PACKAGES` in `src/botsworth/adapters/__init__.py`, and add the default log folder to
   `DEFAULT_ROOTS` in `src/botsworth/pointer.py`.
3. Read `docs/specs/data-model.md`, section 3 (adapter capture). It lists, for each stored field, the source field,
   the rule and a test case. Adapters emit canonical records only; the store computes cross-operation values. Add a
   price alias in `src/botsworth/pricing/` if the provider uses model names that are not in the price list.
4. Add a synthetic fixture under `tests/fixtures/` and tests that run it through the real pipeline.
5. If the adapter changes canonical output, bump `PARSER_VERSION` in `src/botsworth/pipeline.py` and check
   `docs/specs/data-model.md` for what a bump does (a schema version bump forces a store rebuild).

The specs do not have a step-by-step guide for a new provider. `docs/design/design.md` says only that a new provider
is one adapter plus a link-basis row. If something in these steps is wrong or missing, open an issue.

## Reporting problems

Use the issue templates. Do not paste prompt text or other private session content into an issue. For security
problems, follow `SECURITY.md`.
