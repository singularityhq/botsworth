"""CONTRACT: `bw`, the helper library preloaded in the REPL namespace (interfaces.md REPL-6, D58).

GUARANTEES:
- Each public method of `BW` is one helper with a one-line docstring; `BW.help()` prints them.
- A helper that works on rows takes a frame as its first argument and defaults to the full frame
  (`issued(tc=None)`, `prev(frame=None)`, `cache_writes(frame=None)`; `first_after(event_kind, frame=None)`
  and `what_if(frame, model)` take the frame after a required argument as the interfaces.md signature lists
    them).
  A helper returns a new DataFrame or a small record and never changes a frame. It prints nothing but `help`.
- The helpers read the frames held by `BW` (set at load and by `reload`), not the names in the user namespace,
  so
  a cell that rebinds `calls` does not change what `bw.cache_writes()` sees.
- `what_if` prices through `botsworth.pricing.Pricer`, the one price calculation. `text` resolves a `src://`
  pointer through `botsworth.pointer`. Neither is reimplemented here.
- Count-once is the frames': `issued` returns each call once however many tool calls it issued.
EXPECTS: `frames` holds the seven DataFrames of `query.frames.NAMES`; `reload_fn` returns a dict of the same.
FAILURE BEHAVIOR: an unknown or ambiguous session prefix raises `ValueError` naming the matches; an unknown
  frame
  name raises `KeyError`; `text` raises `PointerError` / `SourceMissing` from `pointer`. A call whose model
    has no
  price at its provider keeps its stored cost in `what_if` and gets `repriced` False.
DOES NOT: read the store (the frames are in memory), print prose, change a frame, or offer SQL.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from botsworth import pointer
from botsworth.pricing import Pricer
from botsworth.query.frames import NAMES

TRAPS = """Traps:
  - Cost counts once per request: the frames do it. Never sum incl_cost_usd over nested rows (threads,
    sessions): it repeats the children.
  - `effect` is a '+'-joined set ('change+look'), empty for a call with no tool calls.
  - tool_name case varies (Bash/bash): compare with .str.lower().
  - To go from tool calls to calls use bw.issued(tool_calls[...]); a plain merge repeats a call once per tool
    call.
  - local_day / local_hour are columns of calls, in the session time zone you started with.
Column notes:
  - gap_ms = call start minus the previous call's end in the thread; negative when calls overlap.
  - in_tok is uncached input only; cache_read_tok / cache_write_* are separate (context_tok adds them).
  - call_cost_share_usd (tool_calls) = the issuing call's cost split over its tool calls; calls that issued
    none are not in tool_calls, so the shares sum to less than total call cost.
  - sessions cost columns are NULL for a session with no LLM calls (n_calls 0).
  - agent_type is NULL for Claude (adapter gap).
  - bw.cache_writes(frame): calls that wrote cache and read none; works on any calls frame you pass.
Examples:
  daily total:  bw.by(calls, "local_day")
  bash calls:   bw.issued(tool_calls[tool_calls.tool_name.str.lower() == "bash"])
  changed:      bw.issued(tool_calls[tool_calls.effect == "change"])
  at Sonnet:    bw.what_if(calls, "claude-sonnet-4-5").delta_usd.sum()"""

COST_COLS = [
    "cost_usd",
    "cost_in_usd",
    "cost_out_usd",
    "cost_cache_read_usd",
    "cost_cache_write_usd",
    "call_cost_share_usd",
]
TOKEN_COLS = [
    "in_tok",
    "out_tok",
    "cache_read_tok",
    "cache_write_tok",
    "cache_write_5m_tok",
    "cache_write_1h_tok",
]


PROVIDERS = ("claude_code", "codex", "omp")


def _price(t: pd.DataFrame, rates) -> tuple[np.ndarray, np.ndarray]:
    """Cost of the token columns of `t` at `rates`, as `Pricer.cost` computes it, for all rows at once.

    Returns (cost in USD, priced); a row is not priced when a token count is missing or a used count has
    no rate.
    """

    def col(name: str) -> pd.Series:
        return pd.to_numeric(t[name], errors="coerce").astype("float64")

    n_in, n_out = col("in_tok"), col("out_tok")
    ok = (n_in.notna() & n_out.notna()).to_numpy().copy()
    w5, w1 = col("cache_write_5m_tok"), col("cache_write_1h_tok")
    flat = w5.isna() & w1.isna()
    w5 = w5.where(~flat, col("cache_write_tok"))
    parts = [
        (n_in.fillna(0), rates.input),
        (n_out.fillna(0), rates.output),
        (col("cache_read_tok").fillna(0), rates.cache_read),
        (w5.fillna(0), rates.cache_write_5m),
        (w1.fillna(0), rates.cache_write_1h),
    ]
    total = np.zeros(len(t))
    for count, rate in parts:
        if rate is None:
            ok &= (count == 0).to_numpy()
            rate = 0
        total = total + count.to_numpy() * rate
    return total / 1e6, ok


class Session:
    """One session: `.calls`, `.threads`, `.split`, `.cost`, `.n_calls`, `.sub_cost`, `.nested`."""

    def __init__(self, row: pd.Series, calls: pd.DataFrame, threads: pd.DataFrame):
        self.id, self.title, self.provider = row.session_id, row.title, row.provider
        self.calls, self.threads = calls, threads
        self.cost, self.n_calls = float(calls.cost_usd.sum()), len(calls)
        who = np.where(calls.is_main.astype(bool), "main", "subagents")
        self.split = (
            calls.assign(thread=who)
            .groupby("thread")
            .agg(n_calls=("call_id", "size"), cost_usd=("cost_usd", "sum"))
        )
        # FALLBACK-OK: REPL-6 a session whose calls are all on the main thread has no subagent group: 0
        self.sub_cost = float(self.split.cost_usd.get("subagents", 0.0))
        self.sub_calls = int(self.split.n_calls.get("subagents", 0))  # FALLBACK-OK: REPL-6 as above
        self.nested = threads[threads.depth > 1]

    def __repr__(self) -> str:
        return (
            f"Session {self.id} ({self.provider}) {self.title!r}\n"
            f"  calls={self.n_calls} cost=${self.cost:.4f}"
            f"  subagent calls={self.sub_calls} cost=${self.sub_cost:.4f}\n"
            f"  threads={len(self.threads)} (agent runs={int((self.threads.depth > 0).sum())},"
            f" nested deeper than 1: {len(self.nested)})"
        )


class BW:
    def __init__(self, frames: dict[str, pd.DataFrame], reload_fn: Callable[[], dict[str, pd.DataFrame]]):
        assert set(frames) == set(NAMES), sorted(frames)
        self._frames, self._reload = frames, reload_fn

    def help(self) -> None:
        """List the helpers, the frames and the traps."""
        for name in sorted(n for n in dir(self) if not n.startswith("_")):
            doc = (getattr(self, name).__doc__ or "").strip().splitlines()[:1]
            print(f"bw.{name}: {doc[0] if doc else ''}")
        print("Frames: " + ", ".join(NAMES) + "  (columns: bw.schema('calls'))")
        print(TRAPS)

    def schema(self, frame: str | pd.DataFrame | None = None) -> pd.DataFrame:
        """Columns, types and non-null counts of a frame; no argument: row counts of all frames."""
        if frame is None:
            return pd.DataFrame({"rows": {n: len(self._frames[n]) for n in NAMES}})
        df = self._frames[frame] if isinstance(frame, str) else frame
        return pd.DataFrame({"dtype": df.dtypes.astype(str), "non_null": df.notna().sum()})

    def session(self, id_prefix: str) -> Session:
        """One session by id prefix: calls, cost, main thread against subagents (any depth), nested runs."""
        s = self._frames["sessions"]
        hit = s[s.session_id.str.startswith(id_prefix)]
        if len(hit) != 1:
            raise ValueError(f"{len(hit)} sessions match {id_prefix!r}: {list(hit.session_id[:5])}")
        sid = hit.iloc[0].session_id
        c, t = self._frames["calls"], self._frames["threads"]
        return Session(hit.iloc[0], c[c.session_id == sid], t[t.session_id == sid])

    def by(self, frame: pd.DataFrame, *cols: str) -> pd.DataFrame:
        """Count `n` and the cost columns of a frame grouped by any columns, largest cost first."""
        assert cols, "bw.by needs at least one column to group by"
        agg = {"n": (cols[0], "size")}
        agg |= {c: (c, "sum") for c in COST_COLS if c in frame.columns}
        out = frame.groupby(list(cols), dropna=False).agg(**agg)
        key = next((k for k in ("cost_usd", "call_cost_share_usd") if k in out), "n")
        return out.sort_values(key, ascending=False)

    def issued(self, tc: pd.DataFrame | None = None) -> pd.DataFrame:
        """Calls that issued at least one tool call of `tc` (default all), once each (no fan-out)."""
        tc = self._frames["tool_calls"] if tc is None else tc
        c = self._frames["calls"]
        return c[c.call_id.isin(tc.call_id.unique())]

    def prev(self, frame: pd.DataFrame | None = None) -> pd.DataFrame:
        """Rows of `frame` plus prev_call_id, prev_model, prev_effect, prev_gap_ms."""
        full = self._frames["calls"].sort_values(["thread_id", "thread_pos"])
        g = full.groupby("thread_id", sort=False)
        full = full.assign(
            prev_call_id=g.call_id.shift(1),
            prev_model=g.model.shift(1),
            prev_effect=g.effect.shift(1),
            prev_gap_ms=g.gap_ms.shift(1),
        )
        extra = ["prev_call_id", "prev_model", "prev_effect", "prev_gap_ms"]
        if frame is None:
            return full
        return frame.merge(full[["call_id", *extra]], on="call_id", how="left")

    def first_after(self, event_kind: str, frame: pd.DataFrame | None = None) -> pd.DataFrame:
        """First call of `frame` after each event of that kind in its thread; adds event_id, event_ts_ms."""
        e = self._frames["events"]
        e = e[(e.kind == event_kind) & e.thread_id.notna() & e.ts_ms.notna()]
        e = e[["event_id", "thread_id", "ts_ms"]].rename(columns={"ts_ms": "event_ts_ms"})
        c = self._frames["calls"] if frame is None else frame
        c = c[c.start_ms.notna() & c.thread_id.notna()]
        c = c.assign(start_ms=c.start_ms.astype("int64")).sort_values("start_ms")
        e = e.assign(event_ts_ms=e.event_ts_ms.astype("int64")).sort_values("event_ts_ms")
        m = pd.merge_asof(
            e, c, left_on="event_ts_ms", right_on="start_ms", by="thread_id", direction="forward",
            allow_exact_matches=False,
        )  # fmt: skip
        return m[m.call_id.notna()].drop_duplicates("call_id")

    def cache_writes(self, frame: pd.DataFrame | None = None) -> pd.DataFrame:
        """Calls of `frame` that wrote cache and read none; adds first_in_thread."""
        c = self._frames["calls"] if frame is None else frame
        writes = c.cache_write_tok.fillna(0) + c.cache_write_5m_tok.fillna(0) + c.cache_write_1h_tok.fillna(0)
        hit = c[(c.cache_read_tok.fillna(0) == 0) & (writes > 0)]
        return hit.assign(first_in_thread=hit.thread_pos == 1)

    def what_if(self, frame: pd.DataFrame, model: str, where: pd.Series | None = None) -> pd.DataFrame:
        """Price `frame` tokens at another model's rates; adds new_cost_usd, delta_usd, repriced."""
        pricer = Pricer()
        out = frame.copy()
        if where is None:
            mask = pd.Series(True, index=out.index)
        elif isinstance(where, pd.Series):
            mask = where.reindex(out.index).fillna(False)
        else:
            mask = pd.Series(where, index=out.index)
        mask = mask.astype(bool)
        known = {p: pricer.resolve(model, p) for p in sorted({*PROVIDERS, *out.provider.dropna().unique()})}
        if all(r is None for r in known.values()):
            raise ValueError(
                f"no price for model {model!r} at any provider (check the spelling; see `prices`)"
            )
        new = out.cost_usd.astype("float64").copy()
        done = pd.Series(False, index=out.index)
        for provider, rates in known.items():
            if rates is None:
                continue  # rows of this provider keep their cost, repriced False (REPL-6)
            sel = mask & (out.provider == provider)
            if not sel.any():
                continue
            cost, ok = _price(out.loc[sel], rates)
            idx = out.index[sel][ok]
            new.loc[idx], done.loc[idx] = cost[ok], True
        out["new_cost_usd"], out["repriced"] = new, done
        out["delta_usd"] = out.new_cost_usd - out.cost_usd
        return out

    def text(self, src: str, part: str | None = None, max_bytes: int = 4000) -> str:
        """The source text behind a src:// pointer (a `src` column), read on demand."""
        return pointer.resolve(src, None, part, 0, max_bytes)["text"]

    def reload(self) -> None:
        """Reload the frames from the store (after an ingest); names you defined stay."""
        fresh = self._reload()
        self._frames.update(fresh)
