from pathlib import Path

import platformdirs
import platformdirs.windows
import pytest
from platformdirs.macos import MacOS
from platformdirs.unix import Unix
from platformdirs.windows import Windows

import botsworth
from botsworth.store import location


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv(location.ENV_VAR, raising=False)
    monkeypatch.delenv(location.HOME_ENV_VAR, raising=False)
    location.configure(None)
    yield
    location.configure(None)


def _as(monkeypatch, cls):
    """Make `platformdirs.user_data_dir` answer as it would on the platform `cls`."""
    monkeypatch.setattr(
        platformdirs, "user_data_dir", lambda appname, appauthor=None: cls(appname, appauthor).user_data_dir
    )


def test_macos_default_is_application_support(monkeypatch):
    _as(monkeypatch, MacOS)
    assert (
        location.resolve_db()
        == Path.home() / "Library" / "Application Support" / "botsworth" / "botsworth.sqlite"
    )


def test_linux_default_uses_xdg_data_home_when_set(monkeypatch, tmp_path):
    _as(monkeypatch, Unix)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert location.resolve_db() == tmp_path / "xdg" / "botsworth" / "botsworth.sqlite"


def test_linux_default_without_xdg_data_home_is_local_share(monkeypatch):
    _as(monkeypatch, Unix)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert location.resolve_db() == Path.home() / ".local" / "share" / "botsworth" / "botsworth.sqlite"


def test_windows_default_is_localappdata_without_author_folder(monkeypatch):
    _as(monkeypatch, Windows)
    # the Windows class reads the known-folder API, which exists only on Windows
    monkeypatch.setattr(platformdirs.windows, "get_win_folder", lambda _name: "/c/Users/a/AppData/Local")
    got = location.resolve_db()
    assert got.name == "botsworth.sqlite" and got.parent.name == "botsworth"
    assert got.parent.parent == Path("/c/Users/a/AppData/Local")


@pytest.mark.parametrize("cls", [MacOS, Unix])
def test_no_default_is_directly_under_home(monkeypatch, cls):
    _as(monkeypatch, cls)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    db = location.resolve_db()
    assert db.parent != Path.home() and db.parent.parent != Path.home()
    assert ".old-profiler" not in db.parts


def test_home_env_places_store_in_that_folder(monkeypatch, tmp_path):
    monkeypatch.setenv(location.HOME_ENV_VAR, str(tmp_path / "h"))
    assert location.resolve_db() == tmp_path / "h" / "botsworth.sqlite"
    assert not hasattr(location, "streams_dir")  # D52: no streams folder beside the store


def test_db_env_beats_home_env_and_empty_env_is_unset(monkeypatch, tmp_path):
    monkeypatch.setenv(location.HOME_ENV_VAR, str(tmp_path / "h"))
    monkeypatch.setenv(location.ENV_VAR, "")
    assert location.resolve_db() == tmp_path / "h" / "botsworth.sqlite"
    monkeypatch.setenv(location.ENV_VAR, str(tmp_path / "env.sqlite"))
    assert location.resolve_db() == tmp_path / "env.sqlite"


def test_empty_home_env_is_unset(monkeypatch):
    _as(monkeypatch, MacOS)
    monkeypatch.setenv(location.HOME_ENV_VAR, "")
    assert location.resolve_db().parent.name == "botsworth"
    assert "Application Support" in location.resolve_db().parts


def test_configure_beats_env_and_none_clears(monkeypatch, tmp_path):
    monkeypatch.setenv(location.ENV_VAR, str(tmp_path / "env.sqlite"))
    botsworth.configure(tmp_path / "cfg.sqlite")
    assert location.resolve_db() == tmp_path / "cfg.sqlite"
    botsworth.configure(None)
    assert location.resolve_db() == tmp_path / "env.sqlite"


def test_cli_beats_everything_and_tilde_expands(monkeypatch, tmp_path):
    monkeypatch.setenv(location.ENV_VAR, str(tmp_path / "env.sqlite"))
    monkeypatch.setenv(location.HOME_ENV_VAR, str(tmp_path / "h"))
    location.configure(tmp_path / "cfg.sqlite")
    assert location.resolve_db(str(tmp_path / "cli.sqlite")) == tmp_path / "cli.sqlite"
    assert location.resolve_db("~/x.sqlite") == Path.home() / "x.sqlite"


def test_does_not_create_directories(tmp_path):
    target = tmp_path / "nope" / "db.sqlite"
    assert location.resolve_db(target) == target and not target.parent.exists()
