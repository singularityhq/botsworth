# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

First public release. Alpha: commands, output and the store schema can change before 1.0.

### Added

- Providers: Claude Code, Codex and omp session logs, read from their default folders (read-only).
- Local SQLite store with tokens, cost and time for each session, turn and call. Cost and usage count once per
  requestId group. The store holds no prompt, output, thinking or tool text; long text is read from the original log
  on request. Times are stored as epoch milliseconds, and the time zone is chosen per request.
- Prices from a bundled models.dev snapshot with an alias table; unpriced models show cost as a lower bound.
- Explicit ingest (`botsworth ingest`), incremental by file, with `--rebuild` to build a fresh store and swap it in.
- CLI: `slow`, `breakdown` and `tasks` for common questions, plus read commands for sessions, turns, calls, periods,
  days, facets, search and ingest status. Output is JSON by default, `--table` for people.
- HTTP API and web UI (`botsworth serve`, `127.0.0.1:8420` by default) over the same store, with the UI bundled in the
  wheel.
- Store location: `--db`, `BOTSWORTH_DB`, `BOTSWORTH_HOME`, or the platform data folder (`botsworth paths`).
