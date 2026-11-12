"""CONTRACT: the ingest pipeline — provider files -> adapters -> bundles -> store (design §8, D7, D52).

GUARANTEES:
- `run(providers, roots, force, reprice, rebuild, *, db, pricer)` is the one
  ingest entry point (`botsworth ingest`). `providers` None = every adapter in
  `adapters.PACKAGES`; `roots` overrides `DEFAULT_ROOTS` (= `pointer.DEFAULT_ROOTS`)
  per provider. A missing
  root skips that provider (reported with `skipped`), not an error.
- A discovered bundle is rebuilt iff `force`, or one of its files (the ref's
  files plus paths `source_files` holds for `<provider>/<bundle_id>`; relative
  ones are under the provider root)
  is new, gone, or its `(size, mtime_ns, sha_first_4k)` differs from
  `source_files`. Unchanged bundles are not built.
- A rebuilt bundle goes from the adapter straight to `store.ingest.ingest`, one bundle at a time; no file
  is written beside the store (D52). `BundleRejected` or any adapter exception rejects that bundle (reason
  reported); the run continues. After the run the data folder holds the store file only (plus SQLite's
  own `-wal`/`-shm` and `.lock`).
- The whole run (discovery, builds, store load, `source_files` writes) holds
  the store's ingest lock (`store.ingest.lock`); a concurrent run raises
  `IngestLocked`.
- `store.ingest.ingest` writes `source_files` from the adapters' `source_file` records. Then the pipeline
  upserts a `source_files` row per file (ref files, recorded files, and the
  adapter's `source_file` records) of every built or rejected bundle
  with the stats taken before the build, so a file that grew during the build
  is rebuilt next run. `source_files` is the only change manifest: a bundle that the adapter or the store
  rejected has `reject_reason` set on its rows and is not rebuilt until one of its files changes.
  Bundles are built and loaded oldest first (`adapters.session_start_ms` of the main file, D23), ties by
  provider and bundle id, whatever order the adapters list them in; one without a timestamp goes last.
- Status: a source file is `live` when its bundle had a partial trailing line
  (known only for bundles built in this run) or its mtime is within
  `LIVE_WINDOW_MS` (10 min) of the run's start; otherwise `settled`. Files of
  unchanged bundles are re-evaluated by the mtime rule every run and their row
  is rewritten when the status changes.
- A recorded path of a scanned provider that no longer exists is set `source_missing` (D13), unless its
  unit was skipped in this run (outside the window); its bundle's rows stay in the store until a rebuild,
  which drops them (D52).
  Such rows are rewritten every run while the file is absent, so they survive a later reload of their
  bundle (which deletes the bundle's rows).
- Every run reads `codex.adapter.session_overrides(<codex root>)` when `codex` was scanned (its root exists)
  and passes it to `store.ingest.ingest`, which applies the renames to stored sessions without rebuilding
  bundles (DM-12); the report's `store.overrides_applied` is the number of sessions changed.
- Every run passes `PARSER_VERSION` and the Pricer to
  `store.ingest.ingest`, which writes `meta.parser_version` and
  `meta.price_fingerprint`. No zone is resolved or passed (DM-41).
  `PARSER_VERSION` is one constant for all adapters (they report no version);
  bump it when any adapter's canonical output changes.
- Window (CLI-12, DM-93, D59): `run(..., since=)` takes `Nd`, `YYYY-MM-DD` or `all`, else raises
  `SinceInvalid` (a ValueError) before the lock is taken. `since=None` uses `meta.ingest_since` of the
  existing store (also when the run is a rebuild), `all` means no window. A stored value that is not valid
  raises `StoredSinceInvalid` (a SinceInvalid, `.value` = the stored text) under the lock, before any
  store write or rebuild; `since=` of any valid form recovers. With a window, a discovered unit
  (one `BundleRef`, every provider alike) is dropped from the run when the newest mtime among its files
  is before the cutoff (`Nd`: now - N*24h; date: 00:00 UTC). Such a unit is not built, not counted as
  rebuilt or unchanged, and its stored rows and `source_files` rows keep their values, so it is never marked
  `source_missing`; the one exception is the status of a `live` row, which is set `settled` (stored size,
  mtime and hash unchanged) when `status_of(stat, False)` from the file's current mtime (`os.stat`; the
  file is not opened) says so; an absent or unreadable file keeps its stored status. The value
  in force is written to `meta.ingest_since` (deleted for `all`). The report
  has `window` (None, or `{since, cutoff_ms, outside_window}`) and a per-provider `outside_window`.
- Unreadable files (CLI-12, D59): with a window, discovery reads the mtime of every member file with plain
  `os.stat` and drops a unit outside the window without opening its files. Every member file of a kept unit
  goes through the hashing `stat()` cache once (the change check reuses it). A stat or open error other than
  absence (for example `PermissionError`) does not abort the run: the file goes in the provider's
  `unreadable: [{path, error}]` (`error` = the OS error text) and its whole unit is skipped like one
  outside the window: not built, not counted as rebuilt, unchanged or outside the window, stored rows and
  `source_files` rows untouched (its status is not re-evaluated either), never marked `source_missing`
  (incremental run). A rebuild writes a new store, so it leaves the unit out, like one outside the window.
  A rejected bundle is a different thing: it writes reject rows.
- Mode (DM-4, D52): when `rebuild` is set, or the store is missing, or its `meta.schema_version` differs
  from `schema.SCHEMA_VERSION` (checked read-only), the run is a rebuild: every
  read and write targets `<db>.rebuild`, no `source_files` row is known (every
  bundle is rebuilt), and `store.ingest.finish_rebuild` swaps it onto `<db>`
  at the end. Any exception discards `<db>.rebuild*` and re-raises; `<db>` is
  untouched. Otherwise the run is incremental, in place.
- Returns `{mode: "rebuild"|"incremental", window, providers: {p: {root, discovered, outside_window,
  rebuilt, unchanged, rejected: [{bundle_id, reason}], unreadable: [{path, error}]}}, store:
  IngestResult.to_dict(), ms: {...}}`.
- `progress` (optional, D57) is called with one dict per step, in order after the lock is taken:
  `start {mode, store}` (`store` is the resolved `<db>`, never `<db>.rebuild`),
  `provider {provider, root, skipped}` for a provider whose root is missing,
  `scanning {provider}`, `discovered {provider, bundles, outside_window}`,
  `bundle {done, total, rebuilt, unchanged, rejected}` (`total` excludes units outside the window) after each
  bundle is processed (adapter
  rejections count in `rejected`; store rejections are known only after the run), and
  `store {bundles}` when the bundle stream is exhausted, before the run returns. Calls happen
  outside adapter error handling and under the ingest lock; an exception from `progress`
  propagates and aborts the run like any other failure, so a caller must not raise.
EXPECTS: adapters honour `adapters.Adapter`; source dirs are only read.
FAILURE BEHAVIOR: adapter failures reject one bundle; store failures follow
  store.ingest (rejected bundle keeps old rows; `IngestLocked` propagates). A stat error on a member
  file of a discovered unit skips that unit and is reported (`unreadable`). A stat error on a recorded
  path that is not a member of a discovered unit still aborts the run, in the `source_missing` check and
  in the change check of a kept unit (which stats recorded paths too); that case is rare and deferred.
DOES NOT: parse provider files itself; query; resume from a byte offset;
  write progress itself (the caller renders it).
KNOWN LIMITATIONS: a changed bundle is rebuilt from whole files (design §8
  item 3 prefix-resume deferred). `source_files.status` has no `rejected`
  value; `reject_reason` carries it.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from botsworth import adapters, pointer
from botsworth.adapters import BundleRef
from botsworth.adapters.codex import adapter as codex_adapter
from botsworth.model import Bundle
from botsworth.pricing import Pricer
from botsworth.store import ingest, location

DEFAULT_ROOTS = pointer.DEFAULT_ROOTS  # one owner of provider roots; omp is ~/.omp/agent/sessions

PARSER_VERSION = "2"  # DM-2
LIVE_WINDOW_MS = 10 * 60 * 1000

_NO_START = 2**62  # design §4.6: a bundle with no timestamp sorts last

Stat = tuple[int, int, str]  # size, mtime_ns, sha_first_4k

_DAY_MS = 86_400_000
_SINCE_FORMS = "Nd (for example 10d), YYYY-MM-DD or all"


class SinceInvalid(ValueError):
    """A `--since` value that is not `Nd`, `YYYY-MM-DD` or `all`."""


class StoredSinceInvalid(SinceInvalid):
    """The stored `meta.ingest_since` is not `Nd`, `YYYY-MM-DD` or `all`; `--since` was not given (CLI-12)."""

    def __init__(self, value: str):
        super().__init__(f"stored ingest_since '{value}' is not valid")
        self.value = value


def parse_since(value: str, now_ms: int) -> int | None:
    """The cutoff (epoch ms) of an ingest window value; None for `all` (CLI-12)."""
    if value == "all":
        return None
    if m := re.fullmatch(r"([0-9]+)d", value):
        n = int(m[1])
        if n >= 1:
            return now_ms - n * _DAY_MS
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            pass  # falls through to the error below
        else:
            return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    raise SinceInvalid(f"--since must be {_SINCE_FORMS}, not {value!r}")


# One event per step of a run: start, provider (with `skipped`), discovered, bundle, store.
Progress = Callable[[dict], None]


def _tell(progress: Progress | None, event: str, **fields) -> None:
    if progress is not None:
        progress({"event": event, **fields})


def _stat(path: Path) -> Stat | None:
    try:
        st = path.stat()
        with path.open("rb") as f:
            return st.st_size, st.st_mtime_ns, hashlib.sha256(f.read(4096)).hexdigest()
    except FileNotFoundError:
        return None  # FALLBACK-OK: T11 contract — an absent file is `source_missing`, not an error


def _row(
    path: str, provider: str, key: str, stat: Stat, now_ms: int, status: str, reject_reason: str | None = None
) -> dict:
    size, mtime_ns, sha = stat
    return {
        "path": path,
        "provider": provider,
        "bundle_id": key,
        "size": size,
        "mtime_ns": mtime_ns,
        "sha_first_4k": sha,
        "ingested_at_ms": now_ms,
        "status": status,
        "reject_reason": reject_reason,
    }


def run(
    providers: Iterable[str] | None = None,
    roots: Mapping[str, Path | str] | None = None,
    force: bool = False,
    reprice: bool = False,
    rebuild: bool = False,
    *,
    db: Path | str | None = None,
    pricer: Pricer | None = None,
    progress: Progress | None = None,
    since: str | None = None,
) -> dict:
    db_path = location.resolve_db(db)
    if since is not None:
        parse_since(since, 0)  # refuse a bad value before any store work
    with ingest.lock(db_path):
        # the window in force: the given value, else the stored one (read before a rebuild replaces the store)
        window = since if since is not None else ingest.meta_value(db_path, "ingest_since")
        if window == "all":
            window = None
        elif since is None and window is not None:
            try:
                parse_since(window, 0)
            except SinceInvalid:  # FALLBACK-OK: CLI-12 — an invalid stored window is reported with its value
                raise StoredSinceInvalid(window) from None
        if not rebuild and not ingest.needs_rebuild(db_path):
            _tell(progress, "start", mode="incremental", store=str(db_path))
            return {"mode": "incremental"} | _run(
                providers, roots, force, reprice, db_path, pricer, progress, window
            )
        _tell(progress, "start", mode="rebuild", store=str(db_path))
        target = ingest.rebuild_target(db_path)
        try:
            report = _run(providers, roots, force, reprice, target, pricer, progress, window)
            ingest.finish_rebuild(db_path)
        except BaseException:
            ingest.discard_rebuild(db_path)
            raise
        return {"mode": "rebuild"} | report


def _run(
    providers,
    roots,
    force,
    reprice,
    store: Path,
    pricer,
    progress: Progress | None = None,
    window: str | None = None,
) -> dict:
    """One run against `store` (= the db, or `<db>.rebuild`)."""
    t_start = time.perf_counter()
    pricer = pricer or Pricer()
    wanted = list(providers) if providers is not None else list(adapters.PACKAGES)
    root_of = {**DEFAULT_ROOTS, **{p: Path(r) for p, r in (roots or {}).items()}}
    known = ingest.source_files(store)
    now_ms = int(time.time() * 1000)
    live_from_ns = (now_ms - LIVE_WINDOW_MS) * 1_000_000
    cutoff_ms = None if window is None else parse_since(window, now_ms)
    outside = 0
    outside_keys: set[str] = set()  # units skipped for the window (D59)
    unreadable_keys: set[str] = set()  # units skipped for an unreadable file (D59)

    def status_of(s: Stat, partial: bool) -> str:
        return "live" if partial or s[1] >= live_from_ns else "settled"

    def status_of_mtime(full: Path) -> str:
        """The status a skipped unit's file has now, from its mtime alone: the file is not opened (D59)."""
        try:
            return status_of((0, os.stat(full).st_mtime_ns, ""), False)
        except (
            OSError
        ):  # FALLBACK-OK: D59 — a skipped unit's absent or unreadable file keeps its stored status
            return "live"

    ms = {"discover": 0.0, "build": 0.0}
    report: dict[str, dict] = {}
    rows: list[dict] = []
    rows_of: dict[str, list[dict]] = {}  # bundle key -> its rows, for a store rejection found after the load
    scanned: list[str] = []
    stats: dict[str, Stat | None] = {}

    def stat(path: str, root: Path) -> Stat | None:
        """Stats of a member (absolute) or recorded path; relative recorded paths are under `root`."""
        full = str(root / path)  # an absolute `path` replaces `root`
        if full not in stats:
            stats[full] = _stat(Path(full))
        return stats[full]

    for provider in wanted:
        root = root_of[provider].expanduser()
        rep: dict = {
            "root": str(root),
            "discovered": 0,
            "outside_window": 0,
            "rebuilt": 0,
            "unchanged": 0,
            "rejected": [],
            "unreadable": [],
        }
        report[provider] = rep
        if root.is_dir():
            scanned.append(provider)
        else:
            rep["skipped"] = "root missing"
            _tell(progress, "provider", provider=provider, root=str(root), skipped="root missing")

    def unit_state(ref: BundleRef, root: Path, rep: dict) -> str:
        """ "unreadable", "outside" or "kept" (D59). With a window, plain `os.stat` mtimes decide first, so an
        out-of-window unit costs no file read; only a kept unit's members go through the hashing `stat()`
        cache that the change check reuses. A stat or open error other than absence marks the file
        unreadable (each one is reported) and the whole unit skipped."""
        paths = [str(Path(f).absolute()) for f in ref.files]
        unreadable = False

        def fail(path: str, exc: OSError) -> None:
            nonlocal unreadable
            assert exc.strerror is not None, f"OSError without text for {path}: {exc!r}"
            rep["unreadable"].append({"path": path, "error": exc.strerror})
            unreadable = True

        if cutoff_ms is not None:
            newest = 0
            for path in paths:
                try:
                    newest = max(newest, os.stat(path).st_mtime_ns)
                except FileNotFoundError:
                    continue  # FALLBACK-OK: D59 — absent file: no mtime, unit judged by its others
                except OSError as exc:  # FALLBACK-OK: D59 — an unreadable file skips its unit and is reported
                    fail(path, exc)
            if unreadable:
                return "unreadable"
            if newest != 0 and newest < cutoff_ms * 1_000_000:
                return "outside"
        for path in paths:
            try:
                stat(path, root)
            except OSError as exc:  # FALLBACK-OK: D59 — an unreadable file skips its unit and is reported
                fail(path, exc)
        return "unreadable" if unreadable else "kept"

    def built_bundles() -> Iterator[Bundle]:
        """Discover all scanned providers, sort by session start, build the changed bundles one at a time."""
        nonlocal outside
        found: list[tuple[int, str, str, BundleRef]] = []
        recorded: dict[str, dict[str, list[str]]] = {}  # provider -> bundle key -> recorded paths
        for provider in scanned:
            t = time.perf_counter()
            _tell(progress, "scanning", provider=provider)
            root = root_of[provider].expanduser()
            refs = list(adapters.get(provider).discover(root))
            ms["discover"] += (time.perf_counter() - t) * 1000
            rep = report[provider]
            states = {ref.bundle_id: unit_state(ref, root, rep) for ref in refs}
            kept = [ref for ref in refs if states[ref.bundle_id] == "kept"]
            n_outside = sum(st == "outside" for st in states.values())
            outside_keys.update(f"{provider}/{b}" for b, st in states.items() if st == "outside")
            unreadable_keys.update(f"{provider}/{b}" for b, st in states.items() if st == "unreadable")
            rep["discovered"] = len(refs)
            rep["outside_window"] = n_outside
            outside += n_outside
            _tell(progress, "discovered", provider=provider, bundles=len(refs), outside_window=n_outside)
            by_key = recorded[provider] = {}
            for path, r in known.items():
                if r["provider"] == provider and r["status"] != "source_missing":
                    by_key.setdefault(r["bundle_id"], []).append(path)
            # D23/design §4.6: oldest first, a bundle with no timestamp last; ties by provider and bundle id,
            # so ownership order never depends on directory listing order
            found += [
                (adapters.session_start_ms(ref) or _NO_START, provider, ref.bundle_id, ref) for ref in kept
            ]
        found.sort(key=lambda x: x[:3])
        total, done = len(found), 0
        counts = {"rebuilt": 0, "unchanged": 0, "rejected": 0}
        for _, provider, _, ref in found:
            adapter = adapters.get(provider)
            root = root_of[provider].expanduser()
            rep = report[provider]
            key = f"{provider}/{ref.bundle_id}"
            members = [str(Path(f).absolute()) for f in ref.files]
            paths = dict.fromkeys([*members, *recorded[provider].get(key, ())])
            changed = force or any(
                (k := known.get(p)) is None or stat(p, root) != (k["size"], k["mtime_ns"], k["sha_first_4k"])
                for p in paths
            )
            if not changed:
                rep["unchanged"] += 1
                rows.extend(
                    _row(p, provider, key, s, now_ms, st, known[p]["reject_reason"])
                    for p in paths
                    if (st := status_of(s := stat(p, root), False)) != known[p]["status"]
                )
                done += 1
                counts["unchanged"] += 1
                _tell(progress, "bundle", done=done, total=total, **counts)
                continue
            partial = False
            bundle = None
            reason = None
            t = time.perf_counter()
            try:
                bundle = adapter.build(ref, pricer)
                partial = bundle.n_partial > 0
                paths.update(dict.fromkeys(f.path for f in bundle.source_files))  # adapter-recorded inputs
            except Exception as exc:  # noqa: BLE001 — contract: any adapter failure rejects one bundle
                reason = f"{type(exc).__name__}: {exc}"[:500]
                rep["rejected"].append({"bundle_id": ref.bundle_id, "reason": reason})
            ms["build"] += (time.perf_counter() - t) * 1000
            new = [
                _row(p, provider, key, s, now_ms, status_of(s, partial), reason)
                for p in paths
                if (s := stat(p, root)) is not None
            ]
            rows.extend(new)
            rows_of[key] = new
            if bundle is not None:
                rep["rebuilt"] += 1
            done += 1
            counts["rejected" if bundle is None else "rebuilt"] += 1
            _tell(progress, "bundle", done=done, total=total, **counts)
            if bundle is not None:
                yield bundle
        _tell(progress, "store", bundles=total)

    overrides = {}
    if "codex" in scanned:  # DM-12: renames live outside every bundle, so they are read on every run
        overrides["codex"] = codex_adapter.session_overrides(root_of["codex"].expanduser())
    t = time.perf_counter()
    result = ingest.ingest(
        built_bundles(),
        reprice,
        db=store,
        pricer=pricer,
        parser_version=PARSER_VERSION,
        locked=True,
        overrides=overrides,
        roots={p: str(root_of[p].expanduser()) for p in scanned},
        meta={"ingest_since": window},
    )
    ms["store"] = (time.perf_counter() - t) * 1000 - ms["discover"] - ms["build"]
    for rej in result.rejected:  # the store refused this bundle: do not rebuild it until a file changes
        for row in rows_of[rej["bundle"]]:
            row["reject_reason"] = rej["error"]
    # A recorded file that is gone: its bundle's rows stay until a rebuild (D52). A skipped unit (D59) keeps
    # its rows and is never marked gone; a window-skipped one only has `live` re-evaluated by file time.
    for provider in scanned:
        root = root_of[provider].expanduser()
        for path, r in known.items():
            key = r["bundle_id"]
            if r["provider"] != provider or key in unreadable_keys:
                continue
            old = (r["size"], r["mtime_ns"], r["sha_first_4k"])
            if key in outside_keys:
                if r["status"] == "live" and status_of_mtime(root / path) != "live":
                    rows.append(
                        _row(path, provider, key, old, r["ingested_at_ms"], "settled", r["reject_reason"])
                    )
            elif stat(path, root) is None:
                rows.append(_row(path, provider, key, old, now_ms, "source_missing", r["reject_reason"]))
    ingest.record_source_files(store, rows)
    ms["total"] = (time.perf_counter() - t_start) * 1000
    win = None if window is None else {"since": window, "cutoff_ms": cutoff_ms, "outside_window": outside}
    return {
        "window": win,
        "providers": report,
        "store": result.to_dict(),
        "ms": {k: round(v, 1) for k, v in ms.items()},
    }
