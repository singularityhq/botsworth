"""CONTRACT: stdlib HTTP server for the UI (interfaces.md §3; wire contract frontend/src/api/types.ts).

GUARANTEES:
- One route table, `ROUTES`, holds every route of §3.2. A route is `Route(method, pattern, view_name,
  parse)`: `parse(path_ids, query)` turns the decoded path segments and the query string into the view's
  keyword arguments (API-2, API-3 via `params.py`). The handler calls `getattr(api, view_name)(**kwargs,
  db=db)`, serialises with `wire.to_json` and contains no query logic (QRY-20). `POST /api/query` gets
  the JSON body as `q`. `availability` takes no `db`.
- Errors are JSON `{"error": "<message>", "code": "<code>"}` (API-4): 400 `bad_request`, 404 `not_found`,
  404 `source_missing`, 405 `method_not_allowed`, 503 `no_store`, 503 `schema_mismatch`, 500 `internal`
  (the traceback goes to stderr; the server stays up). A view still marked `__stub__` answers 501
  `{"error": "not implemented", "code": "internal"}`.
- Store states (API-5, QRY-22): on every route except `ingest/status`, a missing file is 503 `no_store`
  and a store with another schema version is 503 `schema_mismatch`, before the view runs.
- Caching (API-6): a 200 from a GET data route sends `Cache-Control: no-cache` and a weak ETag
  `W/"<api_version>.<schema_version>.<last_ingest_ms>.<price_fingerprint>"` (no zone: it is in the query
  string, so the URL already varies by zone); a matching `If-None-Match` gets 304 and the view does not
  run. `ingest/status`, `source`, and every data route while an ingest holds the lock (QRY-40) send
  `Cache-Control: no-store` and no ETag.
- Responses over 32 KB are gzipped when the request accepts gzip (API-52).
- Path ids are percent-encoded by the client (op ids contain '/' and '#') and decoded per segment; a raw
  '/' inside an id does not match a route (API-1).
- Non-/api GET paths serve `ui_dir` (API-41): a file that exists, else `index.html` (SPA fallback); files
  under `assets/` are immutable for a year, everything else is `no-cache`. With no `ui_dir`, they answer
  404 `{"error": "UI assets not found; build the frontend (make build)", "code": "not_found"}`.
EXPECTS: a store written by `botsworth ingest` (a separate process; SQLite WAL readers see committed
  bundles). `ui_dir` comes from the caller (CLI-10 search order lives in the CLI).
FAILURE BEHAVIOR: see the error mapping above; a request never takes the server down.
DOES NOT: hold a writer connection; contain SQL, aggregation or prose (QRY-20, §5.2).
KNOWN LIMITATIONS: `GET /api/projects` and `GET /api/availability` still return a bare list and a
  `{rows}` object without `freshness` (API-7); they change with API-28 (not Q0).
"""

from __future__ import annotations

import gzip
import importlib
import json
import re
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from botsworth.http import params as P
from botsworth.query import NoStore, NotFound, QueryError, SchemaMismatch, SourceMissing, wire

# `import botsworth.query as m` yields the function: botsworth/__init__ rebinds the name.
query_api = importlib.import_module("botsworth.query")

DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
GZIP_MIN_BYTES = 32 * 1024
NO_UI = "UI assets not found; build the frontend (make build)"

Parse = Callable[[list[str], P.Query], dict[str, Any]]


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str  # `/api/sessions/{session}/turns`; each `{name}` is one percent-encoded segment
    view_name: str
    parse: Parse
    cache: str = "etag"  # `etag` or `no-store` (API-6)
    needs_store: bool = True  # False only for ingest/status (API-5)
    takes_db: bool = True  # False for availability(): a static matrix


_ANY = -(2**63)  # legacy parameters had no lower bound

SORT_KEYS = ("start", "cost", "wall", "active", "tokens")
FIND_KINDS = ("biggest", "waits", "failures", "skills", "agents", "search")
MEASURES = ("cost", "time", "tokens")


def _none(ids: list[str], q: P.Query) -> dict[str, Any]:
    return {}


def _tz_kw(ids: list[str], q: P.Query) -> dict[str, Any]:
    """`tz` only when the request names one; the view takes the machine zone otherwise (API-3, D50)."""
    name = P.tz(q)
    return {} if name is None else {"tz": name}


def _id(name: str) -> Parse:
    return lambda ids, q: {name: ids[0]}


def _with(name: str, extra: Callable[[P.Query], dict[str, Any]]) -> Parse:
    return lambda ids, q: {name: ids[0], **extra(q)}


def _range(q: P.Query) -> dict[str, Any]:
    from_ms, to_ms = P.int_in(q, "from_ms", 0, None), P.int_in(q, "to_ms", 0, None)
    return {"from_ms": from_ms, "to_ms": to_ms}


def _range_pair(q: P.Query) -> dict[str, Any]:
    r = _range(q)
    if (r["from_ms"] is None) != (r["to_ms"] is None):
        raise QueryError("from_ms and to_ms must be given together")
    return r


def _days(ids: list[str], q: P.Query) -> dict[str, Any]:
    window_from, window_to = P.day(q, "window_from"), P.day(q, "window_to")
    if window_from is None or window_to is None:
        raise QueryError("window_from and window_to are required")
    return {
        "scope": P.scope_from_query(q, with_period=False),
        "window_from": window_from,
        "window_to": window_to,
        "bucket": P.choice(q, "bucket", ("day", "week"), "day"),
    }


def _sessions(ids: list[str], q: P.Query) -> dict[str, Any]:
    return {
        "scope": P.scope_from_query(q),
        "sort": P.choice(q, "sort", SORT_KEYS, "start"),
        "dir": P.choice(q, "dir", ("asc", "desc"), "desc"),
        "offset": P.int_in(q, "offset", 0, None, 0),
        "limit": P.int_in(q, "limit", 1, 1000, 200),
        "group": P.choice(q, "group", ("project", "day"), "none"),
        "group_key": P.one(q, "group_key"),
        "group_limit": P.int_in(q, "group_limit", 1, 50, 5),
    }


def _find(ids: list[str], q: P.Query) -> dict[str, Any]:
    kind = P.choice(q, "kind", FIND_KINDS)
    if kind is None:
        raise QueryError("kind is required")
    text = P.one(q, "text", "")
    if kind == "search" and not text:
        raise QueryError("text is required for kind=search")
    return {
        "session": ids[0],
        "kind": kind,
        "measure": P.choice(q, "measure", MEASURES, "cost"),
        "text": text,
        "limit": P.int_in(q, "limit", 1, 10000, 2000),
    }


def _histogram(ids: list[str], q: P.Query) -> dict[str, Any]:
    return {"scope_op": ids[0], "bins": P.int_in(q, "bins", 1, 1000), **_range(q)}


def _source(ids: list[str], q: P.Query) -> dict[str, Any]:
    src = P.one(q, "src")
    if not src:
        raise QueryError("src is required")
    return {
        "id_or_src": src,
        "part": P.choice(q, "part", ("input", "output", "thinking", "text")),
        "offset": P.int_in(q, "offset", 0, None, 0),
        "max_bytes": P.int_in(q, "limit", 1, 1048576, 65536),
    }


def _legacy_sessions(ids: list[str], q: P.Query) -> dict[str, Any]:
    filters = {k: P.one(q, k) for k in ("provider", "project", "account") if P.one(q, k)}
    return {
        **filters,
        "from_ms": P.int_in(q, "from", _ANY, None),
        "to_ms": P.int_in(q, "to", _ANY, None),
        "order": P.one(q, "order") or "-start_ms",
        "limit": P.int_in(q, "limit", _ANY, None, 200),
    }


def _get(pattern: str, view: str, parse: Parse = _none, **kw: Any) -> Route:
    return Route("GET", pattern, view, parse, **kw)


# Order does not matter: patterns are full-path matches.
ROUTES: list[Route] = [
    _get("/api/ingest/status", "ingest_state", cache="no-store", needs_store=False),
    _get("/api/explorer/summary", "period_summary", lambda ids, q: {"scope": P.scope_from_query(q)}),
    _get("/api/explorer/days", "day_series", _days),
    _get("/api/explorer/sessions", "list_sessions", _sessions),
    _get("/api/explorer/facets", "facets", lambda ids, q: {"scope": P.scope_from_query(q)}),
    _get(
        "/api/palette",
        "palette",
        lambda ids, q: {"text": P.one(q, "text", ""), "scope": P.scope_from_query(q)},
    ),
    _get("/api/sessions/{session}", "session_detail", _with("session", lambda q: _tz_kw([], q))),
    _get(
        "/api/sessions/{session}/turns",
        "session_turns",
        _with("session", lambda q: {"min_gap_ms": P.int_in(q, "min_gap_ms", 60000, None, 120000)}),
    ),
    _get("/api/sessions/{session}/find", "find", _find),
    _get("/api/turns/{turn}/rows", "turn_rows", _with("turn", _range_pair)),
    _get("/api/turns/{turn}/histogram", "histogram", _histogram),
    _get("/api/sessions/{session}/histogram", "histogram", _histogram),
    _get(
        "/api/sessions/{session}/resolve",
        "resolve",
        _with("session", lambda q: {"id": P.required(q, "id")}),
    ),
    _get(
        "/api/ops/{engagement}/rows",
        "agent_rows",
        _with(
            "engagement",
            lambda q: {
                "offset": P.int_in(q, "offset", 0, None, 0),
                "limit": P.int_in(q, "limit", 1, 2000, 500),
            },
        ),
    ),
    _get("/api/ops/{op}/detail", "detail", _id("op")),
    _get("/api/source", "source", _source, cache="no-store"),
    Route("POST", "/api/query", "query", _tz_kw, cache="no-store"),
    _get("/api/ops/{op}", "show", _id("op_id")),
    _get(
        "/api/ops/{op}/timeline",
        "timeline",
        _with("op_id", lambda q: {"depth": P.int_in(q, "depth", _ANY, None, 3)}),
    ),
    _get(
        "/api/ops/{op}/outline",
        "outline",
        _with("scope_id", lambda q: {"depth": P.int_in(q, "depth", _ANY, None, 1)}),
    ),
    _get(
        "/api/turns/{turn}/steps",
        "steps",
        _with(
            "turn_id",
            lambda q: {"limit": P.int_in(q, "limit", _ANY, None, 200), "cursor": P.one(q, "cursor")},
        ),
    ),
    _get("/api/sessions", "sessions", _legacy_sessions),
    _get("/api/projects", "projects", _tz_kw),
    _get("/api/availability", "availability", takes_db=False),
]


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(re.sub(r"\{\w+\}", "([^/]+)", pattern))


_COMPILED = [(r, _compile(r.pattern)) for r in ROUTES]


def match_routes(path: str) -> list[tuple[Route, list[str]]]:
    """Every route whose pattern matches `path`, with the decoded path ids."""
    out = []
    for route, rx in _COMPILED:
        m = rx.fullmatch(path)
        if m:
            out.append((route, [unquote(g) for g in m.groups()]))
    return out


def etag_for(fresh: dict[str, Any]) -> str:
    return (
        f'W/"{fresh["api_version"]}.{fresh["schema_version"]}.{fresh["last_ingest_ms"]}'
        f'.{fresh["price_fingerprint"]}"'
    )


_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    500: "internal",
    501: "internal",
    503: "no_store",
}

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


def _content_type(suffix: str) -> str:
    # FALLBACK-OK: interfaces.md API-41 — unknown asset types are served as bytes
    return _TYPES.get(suffix, "application/octet-stream")


def make_handler(api: Any, db: Any, ui_dir: Path | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            self._serve(self.command)

        # every other method gets the JSON 405 of API-4, never stdlib's HTML 501
        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_GET  # noqa: N815

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            # FALLBACK-OK: interfaces.md API-4 — stdlib protocol errors (bad request line) are 400-class
            self._error(code, message or HTTPStatus(code).phrase, _CODES.get(code, "bad_request"))

        # ── dispatch ────────────────────────────────────────────────────────

        def _serve(self, method: str) -> None:
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                if method == "GET":
                    self._guarded(lambda: self._static(url.path))
                else:
                    self._error(405, "use GET", "method_not_allowed")
                return
            matches = match_routes(url.path)
            if not matches:
                self._error(404, f"no route {url.path}", "not_found")
                return
            for route, ids in matches:
                if route.method == method:
                    self._guarded(lambda route=route, ids=ids: self._route(route, ids, url.query))
                    return
            allowed = ", ".join(sorted({r.method for r, _ in matches}))
            self._error(405, f"use {allowed}", "method_not_allowed", {"Allow": allowed})

        def _guarded(self, fn: Callable[[], None]) -> None:
            try:
                fn()
            except SourceMissing as e:  # SourceMissing is not a QueryError
                self._error(404, str(e) or "source file missing", "source_missing")
            except NotFound as e:  # before QueryError: NotFound subclasses it
                self._error(404, str(e) or "not found", "not_found")
            except NoStore as e:
                self._error(503, str(e), "no_store")
            except SchemaMismatch as e:
                self._error(503, str(e), "schema_mismatch")
            except QueryError as e:
                self._error(400, str(e), "bad_request")
            except Exception as e:  # FALLBACK-OK: interfaces.md API-4 — errors are JSON, never HTML
                traceback.print_exc(file=sys.stderr)
                self._error(500, f"{type(e).__name__}: {e}", "internal")

        def _route(self, route: Route, ids: list[str], qs: str) -> None:
            kwargs = route.parse(ids, P.parse_query(qs))
            if route.method == "POST":
                body = self._json_body()
                if isinstance(body, dict) and kwargs.get("tz") and "tz" not in body:
                    body = {**body, "tz": kwargs["tz"]}  # API-3: `?tz=` is the zone when the body names none
                kwargs = {"q": body}
            etag = None
            no_store = route.cache == "no-store"
            if route.needs_store:
                fresh = query_api.check_store(db)  # raises NoStore / SchemaMismatch (QRY-22)
                if route.method == "GET" and not no_store:
                    if query_api.ingest_lock(db).running:
                        no_store = True  # API-6: bundles commit before last_ingest_ms changes
                    else:
                        etag = etag_for(fresh)
            if etag is not None and self._matches(etag):
                self._send(304, b"", etag=etag)
                return
            fn = getattr(api, route.view_name)
            if getattr(fn, "__stub__", False):
                self._error(501, "not implemented", "internal")
                return
            result = fn(**kwargs, db=db) if route.takes_db else fn(**kwargs)
            self._send(200, wire.to_json(result), etag=etag, no_store=no_store or etag is None)

        def _json_body(self) -> dict[str, Any]:
            # FALLBACK-OK: interfaces.md API-4 — no Content-Length is an empty body, rejected as 400 below
            length = int(self.headers.get("content-length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"null")
            except json.JSONDecodeError as e:
                raise QueryError(f"invalid JSON: {e.msg}") from None
            if not isinstance(body, dict):
                raise QueryError("request body must be a JSON object")
            return body

        def _matches(self, etag: str) -> bool:
            # FALLBACK-OK: interfaces.md API-6 — no If-None-Match header means no conditional request
            sent = self.headers.get("if-none-match") or ""
            return etag in [t.strip() for t in sent.split(",")]

        # ── responses ───────────────────────────────────────────────────────

        def _send(
            self,
            status: int,
            body: bytes,
            *,
            etag: str | None = None,
            no_store: bool = False,
            ctype: str = "application/json",
            cache: str | None = None,
            extra: dict[str, str] | None = None,
        ) -> None:
            headers = dict(extra or {})
            if etag is not None:
                headers["ETag"] = etag
            if cache is not None:
                headers["Cache-Control"] = cache
            elif etag is not None:
                headers["Cache-Control"] = "no-cache"
            elif no_store:
                headers["Cache-Control"] = "no-store"
            if (
                status == 200
                and len(body) > GZIP_MIN_BYTES
                # FALLBACK-OK: interfaces.md API-52 — no Accept-Encoding header means no gzip
                and "gzip" in (self.headers.get("accept-encoding") or "")
            ):
                body = gzip.compress(body, compresslevel=5)
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
            self.send_response(status)
            if status != 304:
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if status != 304:
                self.wfile.write(body)

        def _error(self, status: int, message: str, code: str, extra: dict[str, str] | None = None) -> None:
            body = wire.to_json({"error": message, "code": code})
            self._send(status, body, extra=extra)

        def _static(self, path: str) -> None:
            if ui_dir is None or not (ui_dir / "index.html").is_file():
                self._error(404, NO_UI, "not_found")
                return
            root = ui_dir.resolve()
            target = (root / unquote(path).lstrip("/")).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                target = root / "index.html"  # SPA route (API-41)
            immutable = target.is_relative_to(root / "assets")
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            self._send(200, target.read_bytes(), ctype=_content_type(target.suffix), cache=cache)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def make_server(
    host: str = "127.0.0.1",
    port: int = 8420,
    db: Any = None,
    ui_dir: Path | None = None,
    *,
    api: Any = query_api,
) -> ThreadingHTTPServer:
    """`db` is the resolved store (D20). `ui_dir` None serves the API only. Tests pass a fake `api`."""
    return ThreadingHTTPServer((host, port), make_handler(api, db, ui_dir))
