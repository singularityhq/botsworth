"""CONTRACT: dated local models.dev price snapshots and the alias table (design §4.7, D6, D9, D11).

GUARANTEES:
- `fetch(date)` downloads https://models.dev/api.json, keeps only the catalog
  providers the Pricer reads (`CATALOG_PROVIDERS`), and writes
  `data/models.dev-<date>.json` atomically (temp file + rename). Only called by
  hand (`python -m botsworth.pricing.snapshot [DATE]`), never at import or ingest.
- `main(argv)` is that command: optional `DATE` (default today), prints the written
  path. `--help` prints usage and exits 0 without a network call or a write; an
  unknown option exits 2 the same way.
- `latest()` returns the newest dated snapshot in `data/`.
- `ALIASES_PATH` is the explicit alias table: per catalog provider
  `alias -> model_id`, plus `no_premium_1m` (models whose `[1m]` variant bills
  at base rates). No substring or pattern matching anywhere.
EXPECTS: models.dev `cost.*` values are USD per million tokens.
FAILURE BEHAVIOR: fetch errors propagate (the caller keeps the last snapshot,
  design §9); no snapshot in `data/` -> FileNotFoundError.
DOES NOT: price anything; run at import time.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from datetime import date as Date
from pathlib import Path

DATA = Path(__file__).with_name("data")
ALIASES_PATH = DATA / "aliases.json"
MODELS_DEV_URL = "https://models.dev/api.json"
USER_AGENT = "botsworth/0 (+https://models.dev)"  # models.dev answers 403 to urllib's default agent
CATALOG_PROVIDERS = ("anthropic", "openai")


def trim(catalog: dict) -> dict:
    return {k: catalog[k] for k in CATALOG_PROVIDERS}


def write(catalog: dict, day: str, data_dir: Path = DATA) -> Path:
    target = data_dir / f"models.dev-{day}.json"
    fd, tmp = tempfile.mkstemp(dir=data_dir, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(trim(catalog), f, sort_keys=True, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    return target


def fetch(day: str | None = None, data_dir: Path = DATA) -> Path:
    req = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        catalog = json.load(resp)
    return write(catalog, day or Date.today().isoformat(), data_dir)


def latest(data_dir: Path = DATA) -> Path:
    found = sorted(data_dir.glob("models.dev-*.json"))
    if not found:
        raise FileNotFoundError(f"no models.dev snapshot in {data_dir}")
    return found[-1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m botsworth.pricing.snapshot",
        description=(
            f"Download {MODELS_DEV_URL} and write data/models.dev-DATE.json in the package. "
            "Maintainer task, run by hand from a checkout; the only network call in Botsworth."
        ),
    )
    parser.add_argument("date", nargs="?", help="date tag for the file name (default: today, YYYY-MM-DD)")
    args = parser.parse_args(argv)
    print(fetch(args.date))


if __name__ == "__main__":
    main()
