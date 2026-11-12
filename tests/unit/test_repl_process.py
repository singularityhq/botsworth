"""Real-process tests of `botsworth repl` (T1080, interfaces.md REPL-1..REPL-4): every command runs in its own
process against a server process, with TMPDIR and BOTSWORTH_HOME in temp folders. Synthetic store only."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from botsworth import repl
from botsworth.cli import repl as repl_cli
from botsworth.repl import client

T = 60  # seconds a test waits for any one command


@pytest.fixture
def env(tmp_path):
    """A short TMPDIR (the AF_UNIX path limit) and a BOTSWORTH_HOME; every server is killed afterwards."""
    tmpdir = Path(tempfile.mkdtemp(dir="/tmp", prefix="bwr"))
    e = os.environ.copy()
    e["TMPDIR"] = str(tmpdir)
    e["BOTSWORTH_HOME"] = str(tmp_path / "home")
    yield e
    for f in (tmpdir / "botsworth-repl").glob("*.sock") if (tmpdir / "botsworth-repl").is_dir() else []:
        r = bw(e, "status", "--name", f.stem)
        for line in r.stdout.splitlines():
            if " pid " in line:
                try:
                    os.kill(int(line.split(" pid ")[1].split()[0]), signal.SIGKILL)
                except ProcessLookupError:
                    pass
    shutil.rmtree(tmpdir, ignore_errors=True)


def bw(env, *args, db="x", stdin=None, timeout=T):
    cmd = [sys.executable, "-m", "botsworth.cli.main"]
    if db is not None:
        cmd += ["--db", str(db)]
    cmd += ["repl", *args]
    return subprocess.run(cmd, env=env, input=stdin, capture_output=True, text=True, timeout=timeout)


def pid_of(env, name):
    for line in bw(env, "status", "--name", name).stdout.splitlines():
        if " pid " in line:
            return int(line.split(" pid ")[1].split()[0])
    raise AssertionError("no pid in status")


def start(env, db, *args):
    r = bw(env, "start", "--tz", "UTC", *args, db=db)
    assert r.returncode == 0, r.stderr
    return r


# ---- exit 0, state kept, no --name, parallel sessions, stop, exit 2 ----
def test_lifecycle_state_kept_and_stop_then_exit_2(env, repl_db):
    r = start(env, repl_db, "--name", "a")
    assert "frames:" in r.stdout and "calls" in r.stdout
    assert bw(env, "run", "x = 41", "--name", "a").returncode == 0
    r = bw(env, "run", "x + 1")  # no --name: the only session
    assert (r.returncode, r.stdout.strip()) == (0, "42")
    r = bw(env, "run", "-", "--name", "a", stdin="y = len(calls)\ny > 0")
    assert (r.returncode, r.stdout.strip()) == (0, "True")
    assert bw(env, "stop").returncode == 0
    r = bw(env, "run", "x")
    assert r.returncode == 2 and "state is lost" in r.stderr
    assert not (tmpdir_of(env) / "botsworth-repl" / "a.sock").exists()


def tmpdir_of(env):
    return Path(env["TMPDIR"])


def test_two_named_sessions_are_isolated_and_no_name_is_ambiguous(env, repl_db):
    start(env, repl_db, "--name", "one")
    start(env, repl_db, "--name", "two")
    bw(env, "run", "v = 1", "--name", "one")
    bw(env, "run", "v = 2", "--name", "two")
    assert bw(env, "run", "v", "--name", "one").stdout.strip() == "1"
    assert bw(env, "run", "v", "--name", "two").stdout.strip() == "2"
    r = bw(env, "run", "v")
    assert r.returncode == 4 and "one" in r.stderr and "two" in r.stderr
    assert bw(env, "stop", "--name", "one").returncode == 0
    assert bw(env, "run", "v").stdout.strip() == "2"  # one session left: no --name needed


# ---- exit 1, cut, memory warning ----
def test_cell_raises_exit_1_and_session_keeps_state(env, repl_db):
    start(env, repl_db, "--name", "a")
    bw(env, "run", "k = 5", "--name", "a")
    r = bw(env, "run", "1/0", "--name", "a")
    assert r.returncode == 1 and "ZeroDivisionError" in r.stdout
    assert bw(env, "run", "k", "--name", "a").stdout.strip() == "5"


def test_output_cut_at_20000_characters(env, repl_db):
    start(env, repl_db, "--name", "a")
    r = bw(env, "run", "print('x' * 30000)", "--name", "a")
    assert r.returncode == 0
    assert "[output cut: " in r.stdout and len(r.stdout) < 20300


def test_memory_warning_after_cell_over_max_rss(env, repl_db):
    start(env, repl_db, "--name", "a", "--max-rss", "1")
    r = bw(env, "run", "big = pd.DataFrame({'a': range(1000)})", "--name", "a")
    assert r.returncode == 0 and "[warning: server memory" in r.stdout and "big" in r.stdout


# ---- exit 3: wait exceeded, busy, loading; caller killed -> status --last; interrupt ----
def test_wait_exceeded_busy_status_last_and_interrupt(env, repl_db):
    start(env, repl_db, "--name", "a")
    r = bw(env, "run", "import time; time.sleep(60)", "--name", "a", "--wait", "0.5")
    assert r.returncode == 3 and "continues" in r.stderr
    r = bw(env, "run", "1", "--name", "a")
    assert r.returncode == 3 and "busy" in r.stderr
    assert bw(env, "status", "--name", "a", "--last").returncode == 3
    r = bw(env, "interrupt", "--name", "a")
    assert r.returncode == 0 and "interrupt sent" in r.stdout
    deadline = time.time() + 15
    while time.time() < deadline:
        r = bw(env, "status", "--name", "a", "--last")
        if r.returncode != 3:
            break
        time.sleep(0.2)
    assert r.returncode == 1 and "KeyboardInterrupt" in r.stdout
    bw(env, "run", "z = 3", "--name", "a")
    assert bw(env, "run", "z", "--name", "a").stdout.strip() == "3"  # session survived
    assert bw(env, "interrupt", "--name", "a").stdout.strip() == "no cell is running"


def test_caller_killed_result_is_read_with_status_last(env, repl_db):
    start(env, repl_db, "--name", "a")
    cmd = [sys.executable, "-m", "botsworth.cli.main", "--db", str(repl_db), "repl"]
    p = subprocess.Popen(
        [*cmd, "run", "import time; time.sleep(2); print('finished')", "--name", "a", "--wait", "30"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    p.kill()
    p.wait()
    deadline = time.time() + 20
    while time.time() < deadline:
        r = bw(env, "status", "--name", "a", "--last")
        if r.returncode != 3:
            break
        time.sleep(0.2)
    assert r.returncode == 0 and "finished" in r.stdout


def test_run_while_loading_exit_3(env):
    # a server that is still loading cannot be timed on a small store: a socket that answers "loading"
    # stands in for it
    sd = tmpdir_of(env) / "botsworth-repl"
    sd.mkdir(mode=0o700)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sd / "l.sock"))
    srv.listen(4)

    def answer():
        for _ in range(1):
            c, _ = srv.accept()
            req = json.loads(c.makefile().readline())
            reply = {"ok": True, "loading": True} if req["op"] == "run" else {"ok": True, "ready": False}
            c.sendall(json.dumps(reply).encode() + b"\n")
            c.close()

    threading.Thread(target=answer, daemon=True).start()
    r = bw(env, "run", "1", "--name", "l")
    srv.close()
    assert r.returncode == 3 and "still loading" in r.stderr


# ---- exit 4 ----
def test_start_with_different_params_exit_4_and_equal_params_reuses(env, repl_db):
    start(env, repl_db, "--name", "a")
    pid = pid_of(env, "a")
    r = bw(env, "start", "--tz", "UTC", "--name", "a", db=repl_db)
    assert r.returncode == 0 and pid_of(env, "a") == pid  # no second load
    r = bw(env, "start", "--tz", "Asia/Kolkata", "--name", "a", db=repl_db)
    assert r.returncode == 4 and "different parameters" in r.stderr and "tz" in r.stderr
    r = bw(env, "start", "--tz", "UTC", "--name", "a", "--since", "2020-01-01", db=repl_db)
    assert r.returncode == 4 and "since" in r.stderr
    assert pid_of(env, "a") == pid


# ---- exit 2 ----
def test_no_session_exit_2_for_every_command(env):
    for args in (("run", "1"), ("status",), ("status", "--last"), ("interrupt",), ("stop",)):
        r = bw(env, *args)
        assert r.returncode == 2 and "state is lost" in r.stderr, args
    for args in (("run", "1"), ("status",), ("interrupt",), ("stop",)):
        assert bw(env, *args, "--name", "ghost").returncode == 2


def test_server_killed_exit_2_and_stale_socket_removed(env, repl_db):
    start(env, repl_db, "--name", "a")
    os.kill(pid_of(env, "a"), signal.SIGKILL)
    sock = tmpdir_of(env) / "botsworth-repl" / "a.sock"
    time.sleep(0.3)
    assert sock.exists()  # the dead server left its socket
    r = bw(env, "run", "1", "--name", "a")
    assert r.returncode == 2 and "state is lost" in r.stderr
    assert not sock.exists()


def test_idle_session_stops_itself(env, repl_db):
    start(env, repl_db, "--name", "a", "--idle", "0.03")  # 1.8 s; `status` does not count as use
    deadline = time.time() + 20
    while time.time() < deadline:
        r = bw(env, "status", "--name", "a")
        if r.returncode == 2:
            break
        time.sleep(0.5)
    assert r.returncode == 2 and "state is lost" in r.stderr


# ---- exit 5 ----
def test_start_without_store_exit_5(env, tmp_path):
    r = bw(env, "start", "--tz", "UTC", db=tmp_path / "missing.sqlite")
    assert r.returncode == 5 and "botsworth ingest" in r.stderr


def test_start_with_other_schema_exit_5(env, repl_db, tmp_path):
    other = tmp_path / "old.sqlite"
    shutil.copy(repl_db, other)
    con = sqlite3.connect(other)
    con.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    r = bw(env, "start", "--tz", "UTC", "--name", "a", db=other)
    assert r.returncode == 5 and "schema" in r.stderr
    assert not (tmpdir_of(env) / "botsworth-repl" / "a.sock").exists()


def test_start_socket_folder_unusable_exit_5(env, repl_db):
    (tmpdir_of(env) / "botsworth-repl").write_text("a file, not a folder")
    r = bw(env, "start", "--tz", "UTC", "--name", "a", db=repl_db)
    assert r.returncode == 5 and "socket folder" in r.stderr


def test_start_socket_path_too_long_exit_5(env, repl_db):
    long = tmpdir_of(env) / ("d" * 110)
    long.mkdir()
    env = {**env, "TMPDIR": str(long)}
    r = bw(env, "start", "--tz", "UTC", "--name", "a", db=repl_db)
    assert r.returncode == 5 and "session socket" in r.stderr


def test_server_bind_failure_ends_with_reason(env, repl_db):
    """The server's own bind failure (what `start` reports as exit 5 when the client check passes)."""
    long = tmpdir_of(env) / ("d" * 110)
    long.mkdir()
    env = {**env, "TMPDIR": str(long)}
    r = subprocess.run(
        [sys.executable, "-m", "botsworth.repl.server", "--db", str(repl_db), "--tz", "UTC", "--name", "a"],
        env=env, capture_output=True, text=True, timeout=T,
    )  # fmt: skip
    assert r.returncode == 1 and "error: cannot create the session socket" in r.stderr


def test_start_bad_name_exit_5(env, repl_db):
    r = bw(env, "start", "--tz", "UTC", "--name", "bad name!", db=repl_db)
    assert r.returncode == 5 and "session name" in r.stderr


def test_start_without_pandas_exit_5(monkeypatch, capsys, tmp_path):
    db = tmp_path / "s.sqlite"
    db.write_text("")
    monkeypatch.setattr(client.importlib.util, "find_spec", lambda name: None)
    args = type("A", (), {"repl_cmd": "start", "name": None, "since": None, "until": None,
                          "sessions": None, "idle": 20, "max_rss": 2000, "tz": "UTC"})()  # fmt: skip
    assert repl_cli.run(args, str(db)) == repl.EXIT_START_FAILED
    assert "pandas is not installed" in capsys.readouterr().err


def test_exit_codes_are_documented_values():
    assert (repl.EXIT_OK, repl.EXIT_RAISED, repl.EXIT_NO_SESSION) == (0, 1, 2)
    assert (repl.EXIT_BUSY, repl.EXIT_CHOICE, repl.EXIT_START_FAILED) == (3, 4, 5)
