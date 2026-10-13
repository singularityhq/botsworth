# Botsworth

**Botsworth** tells you what your coding agents were worth: where the time, tokens and money went in each session. It works like a good butler: it keeps the books, notices everything, and tells no one but you. Everything stays on your machine.

It reads the session logs that Claude Code, Codex and omp already write, puts the numbers in a local SQLite store, and gives you a web UI and a
command line to look at them. Both read the same store.

Typical questions it answers:

- Which sessions cost the most this week, and which model, project or tool caused it?
- How much of a session's wall time was the model, tools, sub-agents, or waiting for you?
- Which turn, tool call or sub-agent was the largest in one session?
- Which commands, skills or slash commands use the most tokens, cost or time across many sessions?

## Install

Needs Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install botsworth        # once the package is published
```

From source today:

```sh
git clone <this repository> && cd botsworth
make setup                        # uv sync + npm ci in frontend/
make build                        # builds the UI and the wheel into dist/
uv tool install dist/botsworth-*.whl
```

## Quick start

```sh
botsworth paths                   # where the store lives (creates nothing)
botsworth ingest                  # read your provider logs into the store (explicit; nothing ingests on its own)
botsworth serve --open            # UI and API on http://127.0.0.1:8420
```

`botsworth ingest` reads every provider found under the folders listed below. Use `--provider claude_code`
(repeatable; also `codex`, `omp`) to read only some. Run it again to pick up new sessions: only logs that changed
are read again. `botsworth ingest --rebuild` builds a new store from the logs and replaces the old one when it is
done (a running `serve` keeps working); it reads every provider, so it cannot be combined with `--provider`.

Commands for people and agents (add `--table` for readable output; the default is JSON; `--json` is accepted):

```sh
# Where did the time of the last 7 days go? Totals, the slowest sessions, the slowest tools.
botsworth slow --since 7d --table

# The largest turns, rows, tools and sub-agents of one session. SESSION is an id from `botsworth sessions`.
botsworth breakdown <SESSION> --table

# Which tools cost the most? Other keys for --by: command_key, verb, skill, agent_type, slash.
botsworth tasks --by tool --measure cost --since 30d --table
```

`botsworth --help` lists every command, and `botsworth <command> --help` lists its options. Filters shared by the
read commands: `--from`/`--to`, `--since`, `--tz`, `--provider`, `--model`, `--branch`, `--project`.

## Where data lives

`botsworth paths` prints the exact folder. Defaults, from the platform app-data folder:

| OS | Data folder |
|---|---|
| macOS | `~/Library/Application Support/botsworth/` |
| Linux | `$XDG_DATA_HOME/botsworth/`, else `~/.local/share/botsworth/` |
| Windows | `%LOCALAPPDATA%\botsworth\` |

The folder holds `botsworth.sqlite` and SQLite's own `-wal`, `-shm` and `.lock` files, nothing else. Ingest writes no
other files. To use another location: `--db PATH` on any command, or the
environment variable `BOTSWORTH_DB` (the store file) or `BOTSWORTH_HOME` (the folder). Order: `--db`, then
`BOTSWORTH_DB`, then `BOTSWORTH_HOME`, then the default. The store is a cache over your provider logs: you can delete
it and run `botsworth ingest` again, and it is rebuilt from the logs. If you delete a provider log, the sessions it
held stay in the store (marked `source_missing`) until the next `botsworth ingest --rebuild`, which drops them.

## Privacy

- Everything stays on your machine. `serve` listens on `127.0.0.1` unless you pass `--host`.
- Provider log folders are opened read-only. Botsworth never writes to them.
- The store keeps numbers (tokens, cost, times), names, and short previews of at most 160 characters. It does not
  store prompts, model output, thinking, or tool input and output. When you open a detail in the UI or run
  `botsworth source`, the text is read from your original log file at that moment. If you delete or move the log file,
  that text is gone from Botsworth too.
- The only network call in the code is `botsworth.pricing.snapshot.fetch`, which downloads the model price list from
  `https://models.dev/api.json`. It runs only when you start it by hand (`python -m botsworth.pricing.snapshot`).
  Ingest, serve and every query never call it. A price list dated 2026-10-12 ships inside the package.

## Supported providers

| Provider | `--provider` | Log folder read by default |
|---|---|---|
| Claude Code | `claude_code` | `~/.claude/projects` |
| Codex | `codex` | `~/.codex` |
| omp | `omp` | `~/.omp/agent/sessions` |

Cost is the price list applied to the tokens in the log. Where a model has no price, the cost is shown as a lower
bound and the call is counted as unpriced.

## Development

See `CLAUDE.md` for the project map and rules, and `make help` for the commands.

## License

MIT. See `LICENSE`.

## Why the name?

"Bots' worth" is what your agents were worth. "-worth" is also an English butler's name: someone who keeps the household
accounts and is discreet about them. And since this is a look-back tool, there is a light nod to Wordsworth's
"emotion recollected in tranquillity".
