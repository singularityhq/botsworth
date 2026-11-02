# Security policy

## Reporting a vulnerability

Report it privately with GitHub Security Advisories: open the repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue for a security problem.

Reports of conduct problems (see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)) use the same private reporting channel.

Include the Botsworth version, your OS, the steps to reproduce, and what you expected. Do not include real session
text. We will reply in the advisory thread. Botsworth is a 0.1.0 Alpha project maintained by one person, so replies
can take a few days.

## What Botsworth does that matters for security

- **Local server.** `botsworth serve` listens on `127.0.0.1` by default. It has no login. If you pass `--host` with a
  non-loopback address, anyone who can reach that address can read your session data. That is expected behaviour, not a
  vulnerability.
- **Read-only provider logs.** `~/.claude`, `~/.codex` and `~/.omp` are opened read-only. The store is the only thing
  ingest writes.
- **No prompt text in the store.** The store keeps numbers, names and previews of at most 160 characters. Full text is
  read from the original log file on request, through `src://` pointers.
- **One network call.** `botsworth.pricing.snapshot.fetch` downloads the price list from `https://models.dev/api.json`.
  It runs only when you start it by hand. Ingest, serve and queries do not use the network.

## In scope

For example: the server reading a file outside the provider log folders or the UI assets, a pointer that escapes its
root, writes to a provider log folder, or a network call made without a manual start.

## Supported versions

Only the latest release gets fixes.
