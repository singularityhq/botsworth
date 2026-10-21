"""D55 command class (DM-28) and test counts (DM-29): the pure functions of adapters/common/commands.py."""

import pytest

from botsworth.adapters.common.commands import command_class, count_tests


@pytest.mark.parametrize(
    "command,want",
    [
        # the DM-28 tests
        ("cd /r && git status && uv run pytest -q 2>&1 | tail -3", "test"),
        ("timeout 590 uv run python -m pytest tests/x", "test"),
        ("for i in 1 2; do uv run pytest -x; done", "test"),
        ("tail -f pytest.log", None),
        ("grep -rn pytest .", None),
        ("until grep -q passed /tmp/o.log; do sleep 5; done", "wait"),
        ("sleep 30", "wait"),
        ("npm run test:unit", "test"),
        ("git status", None),
        # runners named in the spec
        ("pytest", "test"),
        ("py.test -x", "test"),
        ("vitest run", "test"),
        ("npx jest --ci", "test"),
        ("mocha spec/", "test"),
        ("playwright test", "test"),
        ("go test ./...", "test"),
        ("cargo test --release", "test"),
        ("cargo nextest run", "test"),
        ("tox -e py312", "test"),
        ("nox -s tests", "test"),
        ("python -m unittest discover", "test"),
        ("python3 -m unittest", "test"),
        ("rspec spec", "test"),
        ("phpunit", "test"),
        ("bun test", "test"),
        ("deno test", "test"),
        ("npm test", "test"),
        ("pnpm test", "test"),
        ("yarn test", "test"),
        ("pnpm run test", "test"),
        ("pnpm --prefix frontend exec vitest run", "test"),
        # prefixes and wrappers
        ("FOO=1 BAR=2 pytest", "test"),
        ("time nice -n 5 pytest", "test"),
        ("env CI=1 pytest", "test"),
        ("timeout -s KILL 30 pytest", "test"),
        ("uv run --project x pytest", "test"),
        ("uvx pytest", "test"),
        ("poetry run pytest", "test"),
        ("bunx vitest", "test"),
        ("bash -c 'cd /r && uv run pytest -q'", "test"),
        ('bash -lc "pytest -q"', "test"),
        ("./.venv/bin/pytest -q", "test"),
        # separators
        ("make build; pytest", "test"),
        ("make build || pytest", "test"),
        ("pytest &", "test"),
        ("cd x\npytest\n", "test"),
        ("if true; then pytest; fi", "test"),
        # not a runner
        ("uv run python script.py", None),
        ("echo pytest", None),
        ("git commit -m 'run pytest'", None),
        ("pip install pytest", None),
        ("npm run build", None),
        ("npm install", None),
        ("make test", None),
        ("go build ./...", None),
        ("cargo build", None),
        ("ls 2>&1", None),
        # a heredoc body is no command
        ("git commit -F - <<'EOF'\npytest passed\nEOF", None),
        # wait: sleep or a polling loop, after the test rule
        ("sleep 5 && echo done", "wait"),
        ("while true; do ls; done", "wait"),
        ("timeout 60 sleep 100", "wait"),
        ("sleep 5; pytest", "test"),
        # a prefix with no command after it
        ("timeout 5", None),
        ("env", None),
        ("FOO=1", None),
        ("uv run", None),
        ("time; nice -n 5", None),
        ("npm", None),
    ],
)
def test_command_class(command, want):
    assert command_class(command) == want


@pytest.mark.parametrize("command", [None, "", "   ", 5])
def test_command_class_of_nothing_is_none(command):
    assert command_class(command) is None


def test_command_class_unwraps_an_argv_list():
    assert command_class(["bash", "-lc", "cd /r && pytest -q"]) == "test"
    assert command_class(["git", "status"]) is None


def test_command_class_reads_the_whole_command_not_a_clipped_one():
    # DM-28: computed before any clipping; the runner sits after 3,000 chars
    assert command_class("echo " + "x" * 3000 + " && pytest") == "test"


@pytest.mark.parametrize(
    "output,want",
    [
        # the DM-29 tests
        ("3 failed, 540 passed, 2 errors in 410.2s", (540, 5)),
        ("540 passed in 410.2s", (540, 0)),
        ("Tests  2 failed | 984 passed (986)", (984, 2)),
        ("Tests:       1 failed, 5 passed, 6 total", (5, 1)),
        ("test result: ok. 12 passed; 0 failed; 0 ignored", (12, 0)),
        ("....... [100%]\nsome output cut by tail -1", None),
        # more shapes
        ("=========== 3 failed, 540 passed, 2 errors in 410.2s (0:06:50) ===========", (540, 5)),
        ("1 error in 0.5s", (0, 1)),
        ("12 passed, 1 skipped, 3 warnings in 1.00s", (12, 0)),
        ("\x1b[32m540 passed\x1b[0m\x1b[32m in 410.2s\x1b[0m", (540, 0)),
        ("Test Files  1 failed | 3 passed (4)\n      Tests  2 failed | 984 passed (986)", (984, 2)),
        ("Tests  1 failed | 2 skipped | 984 passed (987)", (984, 1)),
        ("test result: FAILED. 3 passed; 1 failed; 0 ignored\ntest result: ok. 9 passed; 0 failed", (12, 1)),
        ("test result: ok. 12 passed; 1 failed; 0 ignored\ntest result: ok. 0 passed; 0 failed", (12, 1)),
        ("", None),
        (None, None),
        ("5 passed", None),  # no `in Xs`: not a pytest summary line
        ("ok  \tgithub.com/x/y\t0.123s", None),  # go test prints no count line
    ],
)
def test_test_counts(output, want):
    assert count_tests(output) == want


def test_test_counts_takes_the_last_summary_line():
    text = "1 failed, 2 passed in 1.0s\nrerun\n5 passed in 2.0s\ntrailing noise"
    assert count_tests(text) == (5, 0)


def test_many_heredoc_starts_take_linear_time():
    import time

    text = "cat <<EOF1 " * 20_000 + "x" * 1000  # 200 KB, no terminator anywhere
    t = time.perf_counter()
    assert command_class(text) is None
    assert time.perf_counter() - t < 1.0
    text = "cat <<EOF\nx\n" * 20_000  # many lines, no terminator
    t = time.perf_counter()
    assert command_class(text) is None
    assert time.perf_counter() - t < 1.0
    text = "".join(f"cat <<E{i}\nbody\nE{i}\n" for i in range(20_000)) + "pytest"
    t = time.perf_counter()
    assert command_class(text) == "test"
    assert time.perf_counter() - t < 1.0


def test_heredoc_body_is_dropped_up_to_its_terminator_only():
    assert command_class("cat <<EOF\npytest\nEOF\nls") is None
    assert command_class("cat <<'EOF'\nsleep 1\n  EOF\npytest -q") == "test"
    assert command_class("cat <<EOF\npytest\n") == "test"  # no terminator: nothing is dropped
