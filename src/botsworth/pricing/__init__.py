"""CONTRACT: the one Pricer (design §4.7 cost rules, D5, D9, D11).

GUARANTEES:
- `Pricer.resolve(model, provider)` returns `Rates` or None. It looks the model
  up in the catalog provider of the harness (`CATALOG_OF`: claude_code ->
  anthropic, codex -> openai; omp reports its own cost and has none) by exact
  id, then through the explicit alias table only; never by substring. A
  `<model>[1m]` id bills at base rates iff the resolved base is listed in
  `no_premium_1m`, else None. `Rates.price_alias` is the catalog id when it
  differs from `model`.
- `Pricer.cost(tokens, rates)` returns a `Cost` or None. Rates are USD per
  million tokens; total = Σ count x rate / 1e6. Cache writes use the 5m and 1h
  tiers when either tier count is present; with no tier counts the flat
  `cache_write_tok` is priced at the 5m rate and `Cost.flat_cache_write` is set
  (a provenance note). The 1h rate is 2 x input for anthropic (published
  multiplier; models.dev lists no 1h rate) and absent elsewhere.
  `reasoning_tok` is not priced separately (it is part of `out_tok`).
- Unknown model, missing in/out counts, or a positive count whose rate is
  absent -> None (cost `missing`, never 0).
- `fingerprint` = first 16 hex of sha256(snapshot bytes + aliases bytes +
  RULE_VERSION); it changes iff one of the three changes.
EXPECTS: a snapshot shaped `{provider: {"models": {id: {"cost": {...}}}}}`;
  token mappings keyed by the canonical `*_tok` names.
FAILURE BEHAVIOR: unreadable or malformed snapshot/aliases raise at
  construction.
DOES NOT: fetch at import time (see `snapshot.fetch`); decide `cost_basis`
  (`reported` vs `computed` is the adapter's call).
KNOWN LIMITATIONS: context-size price tiers in models.dev are ignored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from botsworth.pricing import snapshot as _snapshot

RULE_VERSION = "v1: per-1M rates; 5m/1h cache tiers, flat write at 5m; anthropic 1h = 2x input; [1m] base"
CATALOG_OF: dict[str, str] = {"claude_code": "anthropic", "codex": "openai"}
ONE_M = "[1m]"


@dataclass(frozen=True, slots=True)
class Rates:
    model_id: str
    price_alias: str | None
    input: float | None
    output: float | None
    cache_read: float | None
    cache_write_5m: float | None
    cache_write_1h: float | None


@dataclass(frozen=True, slots=True)
class Cost:
    cost_usd: float
    cost_in_usd: float
    cost_out_usd: float
    cost_cache_read_usd: float
    cost_cache_write_usd: float
    flat_cache_write: bool


class Pricer:
    def __init__(self, snapshot: Path | str | None = None, aliases: Path | str | None = None):
        snap_path = Path(snapshot) if snapshot is not None else _snapshot.latest()
        alias_path = Path(aliases) if aliases is not None else _snapshot.ALIASES_PATH
        snap_bytes = snap_path.read_bytes()
        alias_bytes = alias_path.read_bytes()
        self.snapshot_path = snap_path
        self.catalog: dict = json.loads(snap_bytes)
        table = json.loads(alias_bytes)
        self.no_premium: frozenset[str] = frozenset(table.pop("no_premium_1m"))
        self.aliases: dict[str, dict[str, str]] = table
        digest = hashlib.sha256(snap_bytes + alias_bytes + RULE_VERSION.encode())
        self.fingerprint = digest.hexdigest()[:16]

    def _lookup(self, catalog: str, model: str) -> str | None:
        models = self.catalog.get(catalog, {}).get("models", {})  # FALLBACK-OK: design §4.7 missing
        if model in models:
            return model
        alias = self.aliases.get(catalog, {}).get(model)  # FALLBACK-OK: design §4.7 aliases only
        return alias if alias in models else None

    def resolve(self, model: str | None, provider: str) -> Rates | None:
        catalog = CATALOG_OF.get(provider)  # FALLBACK-OK: design §4.7 omp is reported-only
        if model is None or catalog is None:
            return None
        if model.endswith(ONE_M):
            model_id = self._lookup(catalog, model.removesuffix(ONE_M))
            if model_id not in self.no_premium:
                return None
        else:
            model_id = self._lookup(catalog, model)
        if model_id is None:
            return None
        cost = self.catalog[catalog]["models"][model_id].get("cost")  # FALLBACK-OK: design §4.7 missing
        if not cost:
            return None
        inp = cost.get("input")
        return Rates(
            model_id=model_id,
            price_alias=model_id if model_id != model else None,
            input=inp,
            output=cost.get("output"),
            cache_read=cost.get("cache_read"),
            cache_write_5m=cost.get("cache_write"),
            cache_write_1h=2 * inp if catalog == "anthropic" and inp is not None else None,
        )

    @staticmethod
    def cost(tokens: Mapping[str, int | None], rates: Rates | None) -> Cost | None:
        if rates is None:
            return None
        n_in, n_out = tokens.get("in_tok"), tokens.get("out_tok")
        if n_in is None or n_out is None:
            return None
        n_read = tokens.get("cache_read_tok") or 0  # FALLBACK-OK: design §4.8 absent read = none read
        n_5m, n_1h = tokens.get("cache_write_5m_tok"), tokens.get("cache_write_1h_tok")
        flat = n_5m is None and n_1h is None
        if flat:
            n_5m = tokens.get("cache_write_tok") or 0  # FALLBACK-OK: design §4.7 flat write at 5m
        parts = [
            ("in", n_in, rates.input),
            ("out", n_out, rates.output),
            ("read", n_read, rates.cache_read),
            ("write", n_5m or 0, rates.cache_write_5m),
            ("write", n_1h or 0, rates.cache_write_1h),
        ]
        micro = {"in": 0.0, "out": 0.0, "read": 0.0, "write": 0.0}
        for name, count, rate in parts:
            if count and rate is None:
                return None
            micro[name] += count * (rate or 0)  # FALLBACK-OK: zero count needs no rate
        return Cost(
            cost_usd=sum(micro.values()) / 1e6,
            cost_in_usd=micro["in"] / 1e6,
            cost_out_usd=micro["out"] / 1e6,
            cost_cache_read_usd=micro["read"] / 1e6,
            cost_cache_write_usd=micro["write"] / 1e6,
            flat_cache_write=flat,
        )
