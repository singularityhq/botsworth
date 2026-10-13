"""CONTRACT: where the store lives (D20, D21).

GUARANTEES:
- `resolve_db(cli_db)` returns the store file by precedence: `cli_db` argument > process override set by
  `configure(db)` > env `BOTSWORTH_DB` (a file) > `BOTSWORTH_HOME` (a folder; the file is `botsworth.sqlite`
  in it) > the platform default. `~` is expanded. An empty env value counts as unset.
- The platform default is `data_dir()/botsworth.sqlite`. `data_dir()` is
  `platformdirs.user_data_dir("botsworth", appauthor=False)`: macOS `~/Library/Application Support/botsworth`,
  Linux `$XDG_DATA_HOME/botsworth` (`~/.local/share/botsworth`), Windows `%LOCALAPPDATA%\\botsworth`.
  Nothing defaults to a folder directly under the home directory.
- `configure(db)` sets the process override; `configure(None)` clears it.
EXPECTS: nothing.
FAILURE BEHAVIOR: none; the path is not checked for existence.
DOES NOT: create directories or files (writers do that); open the database.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

ENV_VAR = "BOTSWORTH_DB"
HOME_ENV_VAR = "BOTSWORTH_HOME"
DB_NAME = "botsworth.sqlite"

_override: Path | None = None


def configure(db: str | Path | None) -> None:
    global _override
    _override = Path(db).expanduser() if db is not None else None


def data_dir() -> Path:
    """The folder for the store: `BOTSWORTH_HOME`, else the platform app-data folder."""
    home = os.environ.get(HOME_ENV_VAR)
    if home:
        return Path(home).expanduser()
    return Path(platformdirs.user_data_dir("botsworth", appauthor=False))


def resolve_db(cli_db: str | Path | None = None) -> Path:
    if cli_db is not None:
        return Path(cli_db).expanduser()
    if _override is not None:
        return _override
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    return data_dir() / DB_NAME
