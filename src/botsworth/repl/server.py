"""CONTRACT: the REPL server, one detached process per session (interfaces.md REPL-3, REPL-4, D58).

Started by the client as `python -m botsworth.repl.server --db P --tz Z --name N ...`; not run by hand.

GUARANTEES:
- It binds `$TMPDIR/botsworth-repl/<name>.sock` (folder 0700, socket 0600) first, then loads the frames with
  `query.frames.load` (read-only store, connection closed after the load), then serves. While it loads,
    `status`
  answers `ready: false` and `run` answers `loading`.
- One namespace for the life of the process: the frames, `bw`, `pd`, `np` and every name a cell defines. One
  cell
  runs at a time on the main thread; the socket is served by other threads, so `status`, `interrupt` and
    `stop`
  answer while a cell runs. A `run` on a busy session answers `busy` at once.
- A cell prints, then the repr of its last expression (like IPython). A cell that raises prints its traceback
  and the namespace keeps every name set before the error. Output over 20,000 characters is cut with a line
    that
  says how many were cut. After a cell, memory over `--max-rss` MB adds a warning naming the biggest frames
  the user defined; nothing is deleted. pandas shows at most 60 rows.
- `run` waits at most `wait` seconds for its cell; if the cell goes on, the reply says `pending` and the
  result
  is kept as `last` for `status --last`, also when the caller has gone.
- `interrupt` raises KeyboardInterrupt in the running cell at its next Python step (a long call in C stops
  when it
  returns); a signal that arrives outside a cell is ignored. `stop` interrupts, then ends the process after 5
    s.
  The process also ends after `--idle` minutes without a `run`, never during a cell.
- It publishes its socket by hard-linking a listening socket from a private name `<name>.<pid base 36>`
  (kept as a second link, so the pid can be found even when the queue is full), never replaces a live socket
  (a second server on the same name ends with exit 17), and on exit removes the socket only if it is still its
  own. Once it has decided to stop, a `run` is refused. `interrupt` of a cell that has not started cancels it.
  It writes nothing else.
EXPECTS: pandas installed; a store of the current schema; a short enough socket path (AF_UNIX limit).
FAILURE BEHAVIOR: cannot create the folder or bind the socket, or the frames do not load (no store, schema
  mismatch, unknown zone or session): one line `error: <reason>` on stderr, exit 1, no socket left. The client
    reports
  it and exits 5. Any failure while answering a request gets `{"ok": false, "error": ...}` and
  the server keeps running. Memory it cannot read is reported as unknown, not as an error.
DOES NOT: touch the store after the load, offer SQL, keep a log, or run a cell on more than one thread.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from botsworth import repl
from botsworth.query import frames as frames_mod
from botsworth.repl import bw as bw_mod

MAX_OUT = 20_000
STOP_GRACE_S = 5.0


def rss_bytes() -> int | None:
    """Resident memory of this process in bytes (/proc where it exists, else `ps`); None when unreadable."""
    statm = Path("/proc/self/statm")
    if statm.exists():
        return int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True
        )
        return int(out.stdout.strip()) * 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        # FALLBACK-OK: REPL-4 memory unreadable (no `ps`): reported as unknown, never an error
        return None


class Server:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.params = {
            "db": str(Path(a.db).resolve()),
            "tz": a.tz,
            "since": a.since,
            "until": a.until,
            "sessions": a.sessions or None,
        }
        self.ns: dict = {}
        self.frames: dict[str, pd.DataFrame] = {}
        self.base_names: set[str] = set()
        self.ready = False
        self.load_s = self.load_cpu_s = 0.0
        self.started = time.time()
        self.last_use = time.time()
        self.jobs: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.busy_since: float | None = None
        self.in_cell = False
        self.job: dict | None = None
        self.ino = -1
        self.stopping = False
        self.last: dict = {"state": "none", "out": "", "dur_s": 0.0}
        self.path = repl.sock_path(a.name)

    # ---- data ----
    def load_frames(self) -> dict[str, pd.DataFrame]:
        p = self.params
        f = frames_mod.load(p["db"], p["tz"], p["since"], p["until"], p["sessions"])
        fresh = {n: getattr(f, n) for n in frames_mod.NAMES}
        self.ns.update(fresh)  # the user's names for the frames follow a reload
        return fresh

    def start_up(self) -> None:
        t0, c0 = time.perf_counter(), time.process_time()
        self.frames = self.load_frames()
        bw = bw_mod.BW(self.frames, self.load_frames)
        self.ns.update(pd=pd, np=np, bw=bw)
        self.load_s, self.load_cpu_s = time.perf_counter() - t0, time.process_time() - c0
        self.base_names = set(self.ns)
        self.ready = True

    # ---- socket threads ----
    def serve(self, sock: socket.socket) -> None:
        while not self.stopping:
            try:
                conn, _ = sock.accept()
            except OSError:
                return  # FALLBACK-OK: REPL-3 the socket was closed because the server is ending
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(1 << 20)
                    if not chunk:
                        return
                    data += chunk
                req = json.loads(data)
                reply = getattr(self, "op_" + req["op"])(req)
            except Exception as e:  # noqa: BLE001
                # FALLBACK-OK: REPL-4 a failed request is reported to the caller; the server stays up
                reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            try:
                conn.sendall(json.dumps(reply).encode() + b"\n")
            except OSError:
                pass  # FALLBACK-OK: REPL-4 the caller went away; a cell's result stays in `last`

    def op_status(self, req: dict) -> dict:
        busy = self.busy_since
        reply = {
            "ok": True,
            "ready": self.ready,
            "pid": os.getpid(),
            "params": self.params,
            "uptime_s": round(time.time() - self.started),
            "rss_mb": None if (rss := rss_bytes()) is None else round(rss / 1e6),
            "idle_s": round(time.time() - self.last_use),
            "busy_s": None if busy is None else round(time.time() - busy, 1),
            "last_state": self.last["state"],
            "last_s": round(self.last["dur_s"], 1),
        }
        if self.ready:
            reply |= {
                "load_s": round(self.load_s, 2),
                "load_cpu_s": round(self.load_cpu_s, 2),
                "user_names": sorted(n for n in set(self.ns) - self.base_names if not n.startswith("_")),
                "banner": bw_mod.TRAPS,
                "rows": {n: len(self.frames[n]) for n in frames_mod.NAMES},
            }
        if req.get("last"):
            reply["last_out"] = self.last["out"]
        return reply

    def op_run(self, req: dict) -> dict:
        code, wait = req["code"], float(req["wait"])  # a bad request fails here, before the session is busy
        with self.lock:
            if self.stopping:
                return {"ok": True, "stopping": True}
            if not self.ready:
                return {"ok": True, "loading": True}
            if self.busy_since is not None:
                return {"ok": True, "busy": True}
            self.busy_since = self.last_use = time.time()
            done = threading.Event()
            job = {"code": code, "done": done, "started": False}
            self.job = job
            self.jobs.put(job)
        if done.wait(wait):
            return {"ok": True, **job["result"]}
        return {"ok": True, "pending": True}

    def cancel_queued(self) -> bool:
        """End a job the main thread has not started (caller holds the lock); True when there was one."""
        job = self.job
        if job is None or job["started"] or job.get("cancelled"):
            return False
        job["cancelled"] = True
        job["result"] = self.last = {
            "state": "interrupted",
            "out": "KeyboardInterrupt: the cell was interrupted before it started.\n",
            "dur_s": 0.0,
        }
        self.busy_since = None
        self.job = None
        job["done"].set()
        return True

    def op_interrupt(self, req: dict) -> dict:
        with self.lock:
            busy = self.busy_since is not None
            if busy and not self.cancel_queued():
                os.kill(os.getpid(), signal.SIGINT)
        return {"ok": True, "was_busy": busy}

    def op_stop(self, req: dict) -> dict:
        with self.lock:
            self.stopping = True
            if self.busy_since is not None and not self.cancel_queued():
                os.kill(os.getpid(), signal.SIGINT)
        timer = threading.Timer(STOP_GRACE_S, self.hard_exit)
        timer.daemon = (
            True  # a normal stop ends the process at once; this only backs up a cell that ignores SIGINT
        )
        timer.start()
        self.jobs.put(None)
        return {"ok": True, "pid": os.getpid()}

    def unlink_own(self) -> None:
        """Remove the socket names only if they are still this server's (a later server may own the name)."""
        repl.private_path(self.path, os.getpid()).unlink(missing_ok=True)
        try:
            if os.stat(self.path).st_ino == self.ino:
                self.path.unlink()
        except FileNotFoundError:
            pass  # FALLBACK-OK: REPL-4 the file is already gone

    def hard_exit(self) -> None:
        self.unlink_own()
        os._exit(0)

    # ---- main thread: run cells ----
    def on_sigint(self, signum: int, frame: object) -> None:
        if self.in_cell:
            self.in_cell = False  # one KeyboardInterrupt per cell
            raise KeyboardInterrupt

    def run_cell(self, code: str) -> dict:
        buf = io.StringIO()
        state = "ok"
        t0 = time.time()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                tree = ast.parse(code, "<cell>", "exec")
                last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                exec(compile(tree, "<cell>", "exec"), self.ns)
                if last is not None:
                    value = eval(compile(ast.Expression(last.value), "<cell>", "eval"), self.ns)
                    if value is not None:
                        self.ns["_"] = value
                        print(repr(value))
            except KeyboardInterrupt as e:
                self.in_cell = False
                state = "interrupted"
                self.print_traceback(e)
                print("KeyboardInterrupt: cell interrupted; names set before this point are kept.")
            except BaseException as e:  # noqa: BLE001  # FALLBACK-OK: REPL-4 a cell may raise anything: report it, keep the session
                self.in_cell = False
                state = "error"
                self.print_traceback(e)
            finally:
                self.in_cell = False
        out = buf.getvalue()
        if len(out) > MAX_OUT:
            out = out[:MAX_OUT] + f"\n[output cut: {len(out) - MAX_OUT} more characters]\n"
        out += self.memory_warning()
        return {"state": state, "out": out, "dur_s": time.time() - t0}

    @staticmethod
    def print_traceback(e: BaseException) -> None:
        tb = e.__traceback__.tb_next if e.__traceback__ else None  # drop this module's frame
        print("".join(traceback.format_exception(type(e), e, tb)), end="")

    def memory_warning(self) -> str:
        rss = rss_bytes()
        if rss is None or rss <= self.a.max_rss * 1e6:
            return ""  # FALLBACK-OK: REPL-4 unknown memory gives no warning
        mine = [
            (n, v.memory_usage(deep=True).sum() / 1e6)
            for n, v in self.ns.items()
            if isinstance(v, pd.DataFrame) and n not in self.base_names
        ]
        top = ", ".join(f"{n} {mb:.0f} MB" for n, mb in sorted(mine, key=lambda x: -x[1])[:3]) or "none"
        return (
            f"[warning: server memory {rss / 1e6:.0f} MB is over the {self.a.max_rss:g} MB limit; "
            f"biggest frames you defined: {top}; nothing was deleted, use `del name` to free memory]\n"
        )

    def main_loop(self) -> None:
        while True:
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                with self.lock:
                    if self.busy_since is None and time.time() - self.last_use > self.a.idle * 60:
                        self.stopping = True  # decided under the lock: a later `run` is refused, not queued
                if self.stopping:
                    return
                continue
            if job is None:
                return
            with self.lock:
                if job.get("cancelled"):
                    continue
                job["started"] = True
                self.in_cell = True
            try:
                res = self.run_cell(job["code"])
            except KeyboardInterrupt:
                # FALLBACK-OK: REPL-4 a stray interrupt ends the cell, not the server
                res = {"state": "interrupted", "out": "KeyboardInterrupt\n", "dur_s": 0.0}
            self.in_cell = False
            with self.lock:
                self.last = res
                self.last_use = time.time()
                job["result"] = res
                self.busy_since = None
                self.job = None
            job["done"].set()


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


class Taken(Exception):
    """A live server already owns the socket name."""


def bind(path: Path) -> tuple[socket.socket, int]:
    """Create the socket folder (0700), then publish a listening socket (0600) under `path` atomically.

    The socket is bound and listening under a private name (`repl.private_path`, kept for the server's life,
    so a client can find the server's pid even when the queue is full) and then hard-linked to `path`: `path`
    never names a socket that is not yet listening. A live socket at `path` is never replaced (`Taken`); a
    dead one is, only while it is still the file that was found dead. Returns the socket and its inode.
    """
    os.umask(0o077)
    tmp = repl.private_path(path, os.getpid())
    at = path.parent
    try:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path.parent, 0o700)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        tmp.unlink(missing_ok=True)
        at = tmp
        sock.bind(str(tmp))
        os.chmod(tmp, 0o600)
        sock.listen(128)
        ino = os.stat(tmp).st_ino
        at = path
        for _ in range(8):
            try:
                os.link(tmp, path)
                # a link has no length limit but a connect has: the name must be usable by a client
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.connect(str(path))
                except OSError:
                    path.unlink()
                    raise
                finally:
                    probe.close()
                break
            except FileExistsError:
                try:
                    found = os.stat(path).st_ino
                except FileNotFoundError:
                    continue  # FALLBACK-OK: REPL-4 it vanished between the two calls: try again
                if not repl.socket_dead(path):
                    raise Taken(path.stem) from None
                with contextlib.suppress(FileNotFoundError):
                    if os.stat(path).st_ino == found:  # still the dead file, not a new server's link
                        path.unlink()
        else:
            raise OSError(f"cannot replace {path}")
    except Taken:
        tmp.unlink(missing_ok=True)
        print(f"error: session {path.stem} is already running", file=sys.stderr)
        sys.exit(repl.EXIT_SERVER_TAKEN)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        fail(f"cannot create the session socket {at}: {e}")
    return sock, ino


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="botsworth.repl.server")
    p.add_argument("--db", required=True)
    p.add_argument("--tz", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--sessions", nargs="*")
    p.add_argument("--idle", type=float, default=20, help="minutes without a run before the server stops")
    p.add_argument("--max-rss", type=float, default=2000, help="MB; a warning follows a cell when above")
    a = p.parse_args(argv)
    srv = Server(a)
    sock, srv.ino = bind(srv.path)
    threading.Thread(target=srv.serve, args=(sock,), daemon=True).start()
    try:
        srv.start_up()
    except Exception as e:  # noqa: BLE001  # FALLBACK-OK: REPL-4 a load failure is reported to the client, then the server ends
        sock.close()
        srv.unlink_own()
        fail(str(e).splitlines()[0] if str(e) else type(e).__name__)
    pd.set_option("display.max_rows", 60)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.width", 200)
    signal.signal(signal.SIGINT, srv.on_sigint)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)  # the client's startup pipe is no longer needed
    try:
        srv.main_loop()
    finally:
        sock.close()
        srv.unlink_own()


if __name__ == "__main__":
    main()
