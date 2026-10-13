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
- A recorded path of a scanned provider that no longer exists is set
  `source_missing` (D13); its bundle's rows stay in the store until a rebuild, which drops them (D52).
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
- Mode (DM-4, D52): when `rebuild` is set, or the store is missing, or its `meta.schema_version` differs
  from `schema.SCHEMA_VERSION` (checked read-only), the run is a rebuild: every
  read and write targets `<db>.rebuild`, no `source_files` row is known (every
  bundle is rebuilt), and `store.ingest.finish_rebuild` swaps it onto `<db>`
  at the end. Any exception discards `<db>.rebuild*` and re-raises; `<db>` is
  untouched. Otherwise the run is incremental, in place.
- Returns `{mode: "rebuild"|"incremental", providers: {p: {root, discovered,
  rebuilt, unchanged, rejected: [{bundle_id, reason}]}}, store:
  IngestResult.to_dict(), ms: {...}}`.
EXPECTS: adapters honour `adapters.Adapter`; source dirs are only read.
FAILURE BEHAVIOR: adapter failures reject one bundle; store failures follow
  store.ingest (rejected bundle keeps old rows; `IngestLocked` propagates).
DOES NOT: parse provider files itself; query; resume from a byte offset.
KNOWN LIMITATIONS: a changed bundle is rebuilt from whole files (design §8
  item 3 prefix-resume deferred). `source_files.status` has no `rejected`
  value; `reject_reason` carries it.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Iterator, Mapping
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
) -> dict:
    db_path = location.resolve_db(db)
    with ingest.lock(db_path):
        if not rebuild and not ingest.needs_rebuild(db_path):
            return {"mode": "incremental"} | _run(providers, roots, force, reprice, db_path, pricer)
        target = ingest.rebuild_target(db_path)
        try:
            report = _run(providers, roots, force, reprice, target, pricer)
            ingest.finish_rebuild(db_path)
        except BaseException:
            ingest.discard_rebuild(db_path)
            raise
        return {"mode": "rebuild"} | report


def _run(providers, roots, force, reprice, store: Path, pricer) -> dict:
    """One run against `store` (= the db, or `<db>.rebuild`)."""
    t_start = time.perf_counter()
    pricer = pricer or Pricer()
    wanted = list(providers) if providers is not None else list(adapters.PACKAGES)
    root_of = {**DEFAULT_ROOTS, **{p: Path(r) for p, r in (roots or {}).items()}}
    known = ingest.source_files(store)
    now_ms = int(time.time() * 1000)
    live_from_ns = (now_ms - LIVE_WINDOW_MS) * 1_000_000

    def status_of(s: Stat, partial: bool) -> str:
        return "live" if partial or s[1] >= live_from_ns else "settled"

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
        rep: dict = {"root": str(root), "discovered": 0, "rebuilt": 0, "unchanged": 0, "rejected": []}
        report[provider] = rep
        if root.is_dir():
            scanned.append(provider)
        else:
            rep["skipped"] = "root missing"

    def built_bundles() -> Iterator[Bundle]:
        """Discover all scanned providers, sort by session start, build the changed bundles one at a time."""
        found: list[tuple[int, str, str, BundleRef]] = []
        recorded: dict[str, dict[str, list[str]]] = {}  # provider -> bundle key -> recorded paths
        for provider in scanned:
            t = time.perf_counter()
            refs = list(adapters.get(provider).discover(root_of[provider].expanduser()))
            ms["discover"] += (time.perf_counter() - t) * 1000
            report[provider]["discovered"] = len(refs)
            by_key = recorded[provider] = {}
            for path, r in known.items():
                if r["provider"] == provider and r["status"] != "source_missing":
                    by_key.setdefault(r["bundle_id"], []).append(path)
            # D23/design §4.6: oldest first, a bundle with no timestamp last; ties by provider and bundle id,
            # so ownership order never depends on directory listing order
            found += [
                (adapters.session_start_ms(ref) or _NO_START, provider, ref.bundle_id, ref) for ref in refs
            ]
        found.sort(key=lambda x: x[:3])
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
                yield bundle

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
    )
    ms["store"] = (time.perf_counter() - t) * 1000 - ms["discover"] - ms["build"]
    for rej in result.rejected:  # the store refused this bundle: do not rebuild it until a file changes
        for row in rows_of[rej["bundle"]]:
            row["reject_reason"] = rej["error"]
    for provider in scanned:  # a recorded file that is gone: its bundle's rows stay until a rebuild (D52)
        root = root_of[provider].expanduser()
        for path, r in known.items():
            if r["provider"] == provider and stat(path, root) is None:
                old = (r["size"], r["mtime_ns"], r["sha_first_4k"])
                rows.append(
                    _row(path, provider, r["bundle_id"], old, now_ms, "source_missing", r["reject_reason"])
                )
    ingest.record_source_files(store, rows)
    ms["total"] = (time.perf_counter() - t_start) * 1000
    return {"providers": report, "store": result.to_dict(), "ms": {k: round(v, 1) for k, v in ms.items()}}
