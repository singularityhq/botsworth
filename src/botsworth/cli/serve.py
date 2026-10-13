"""CONTRACT: `botsworth serve` (CLI-10).

GUARANTEES:
- `find_ui_dir` returns the first existing directory of: `--ui DIR`, env `BOTSWORTH_UI_DIR`, the package's
  bundled `botsworth/http/ui/`, the repo's `frontend/dist`; None when none exists.
- `serve` binds `--host:--port` (port 0 = any free port), prints `botsworth UI on http://<host>:<port>` to
  stderr, then serves the API and the UI until interrupted. With no UI directory it prints one warning
  and serves the API only (API-41). `--open` opens the browser after the bind.
- A port that cannot be bound returns 3 with `error: port <n> in use` (other bind errors name the cause).
DOES NOT: ingest (D5), open a write connection, or hold query logic.
"""

from __future__ import annotations

import argparse
import errno
import importlib
import os
import sys
import webbrowser
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from botsworth.http.server import DIST, NO_UI, make_server

BUNDLED = Path(importlib.import_module("botsworth.http").__file__ or "").parent / "ui"
EXIT_BIND = 3


def find_ui_dir(
    explicit: str | None,
    env: Mapping[str, str] | None = None,
    bundled: Path = BUNDLED,
    repo: Path = DIST,
) -> Path | None:
    env = os.environ if env is None else env
    candidates = [explicit, env.get("BOTSWORTH_UI_DIR"), bundled, repo]
    for c in candidates:
        if c and Path(c).is_dir():
            return Path(c)
    return None


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8420)
    p.add_argument(
        "--ui", metavar="DIR", help="UI assets (default: BOTSWORTH_UI_DIR, bundled, frontend/dist)"
    )
    p.add_argument("--open", action="store_true", help="open the browser after binding")


def serve(args: argparse.Namespace, db: Any, api: Any) -> int:
    ui_dir = find_ui_dir(args.ui)
    if ui_dir is None:
        print(f"warning: {NO_UI}; serving the API only", file=sys.stderr)
    try:
        server = make_server(args.host, args.port, db=db, ui_dir=ui_dir, api=api)
    except OSError as e:
        why = (
            f"port {args.port} in use"
            if e.errno == errno.EADDRINUSE
            else f"cannot bind {args.host}:{args.port}: {e.strerror}"
        )
        print(f"error: {why}", file=sys.stderr)
        return EXIT_BIND
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"botsworth UI on {url}", file=sys.stderr)
    try:
        if args.open:
            webbrowser.open(url)
        server.serve_forever()
    finally:
        server.server_close()
    return 0
