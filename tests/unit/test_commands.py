import pytest

from botsworth.adapters.common.commands import command_key, command_norm, unwrap


def test_d17_examples():
    assert command_norm("cd /r && uv run pytest tests/x -q", "/r") == "uv run pytest tests/x -q"
    assert command_key("cd /r && uv run pytest tests/x -q") == "uv run pytest"


@pytest.mark.parametrize(
    "command",
    [
        ["/bin/zsh", "-lc", "make test"],
        ["bash", "-c", "make test"],
        "bash -lc 'make test'",
        '/bin/zsh -lc "make test"',
    ],
)
def test_shell_wrapper_stripped_argv_and_string(command):
    assert unwrap(command) == "make test"
    assert command_norm(command, None) == "make test"


def test_norm_relativizes_project_paths_collapses_whitespace_and_clips():
    cmd = "cd /p/sub &&  pytest   /p/repo/tests/a.py\n -q --root /p/repo"
    assert command_norm(cmd, "/p/repo/") == "pytest tests/a.py -q --root ."
    assert command_norm("/p/repobar/x", "/p/repo") == "/p/repobar/x"  # prefix of another dir untouched
    assert len(command_norm("echo " + "x" * 300, None)) == 200
    assert command_norm(["git", "status"], None) == "git status"


def test_norm_drops_only_one_leading_cd():
    assert command_norm("cd a && cd b && ls", None) == "cd b && ls"


@pytest.mark.parametrize(
    "command,key",
    [
        ("git commit -m 'x'", "git commit"),
        ("FOO=1 BAR=2 cargo test --release", "cargo test"),
        ("export X=1; npx vitest run", "npx vitest run"),
        ("python -m pytest -q", "python -m pytest"),
        ("uv run python -m pytest", "uv run python -m pytest"),
        ("npx uvx ruff check", "npx uvx ruff check"),
        ("/usr/local/bin/make check", "make check"),
        ("pytest tests/x.py", "pytest"),
        ("uvx ruff check .", "uvx ruff check"),
        ("cd /x", None),
        (["bash", "-lc", "cd /r && pnpm exec tsc -b"], "pnpm exec tsc"),
    ],
)
def test_command_key(command, key):
    assert command_key(command) == key


@pytest.mark.parametrize("bad", [None, "", "   ", 42])
def test_empty_or_non_string_is_none(bad):
    assert command_norm(bad, "/p") is None and command_key(bad) is None
