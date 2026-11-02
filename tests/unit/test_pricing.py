import json
from pathlib import Path

import pytest

from botsworth.pricing import Pricer, snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures"


def usage_tokens(fixture: str) -> dict:
    """Canonical token counts of the final assistant record of a fixture transcript."""
    rows = [json.loads(x) for x in (FIXTURES / fixture / "project" / "s1.jsonl").read_text().splitlines()]
    u = [r for r in rows if r["type"] == "assistant"][-1]["message"]["usage"]
    tiers = u["cache_creation"]
    return {
        "in_tok": u["input_tokens"],
        "out_tok": u["output_tokens"],
        "cache_read_tok": u["cache_read_input_tokens"],
        "cache_write_tok": u["cache_creation_input_tokens"],
        "cache_write_5m_tok": tiers["ephemeral_5m_input_tokens"],
        "cache_write_1h_tok": tiers["ephemeral_1h_input_tokens"],
    }


@pytest.fixture
def fixture_pricer():
    return Pricer(FIXTURES / "F1" / "prices.json")


@pytest.fixture(scope="module")
def real():
    return Pricer()


def test_F1_cost_exact(fixture_pricer):
    cost = fixture_pricer.cost(usage_tokens("F1"), fixture_pricer.resolve("fixture-a", "claude_code"))
    assert cost.cost_usd == 0.00828
    assert cost.flat_cache_write is False


def test_F4_cache_tiers_exact(fixture_pricer):
    cost = fixture_pricer.cost(usage_tokens("F4"), fixture_pricer.resolve("fixture-a", "claude_code"))
    assert cost.cost_cache_write_usd == 0.01575  # 1000 x 3.75 (5m) + 2000 x 6 (1h = 2 x input)
    assert cost.cost_usd == pytest.approx(0.00003 + 0.003 + 0.0015 + 0.01575)


def test_flat_cache_write_priced_at_5m_rate_with_note(fixture_pricer):
    rates = fixture_pricer.resolve("fixture-a", "claude_code")
    cost = fixture_pricer.cost({"in_tok": 0, "out_tok": 0, "cache_write_tok": 1000}, rates)
    assert cost.cost_cache_write_usd == 0.00375 and cost.flat_cache_write is True


def test_unknown_model_and_omp_resolve_to_none(real):
    assert real.resolve("no-such-model", "claude_code") is None
    assert real.resolve("gpt-5", "omp") is None
    assert real.resolve(None, "codex") is None
    assert real.cost({"in_tok": 1, "out_tok": 1}, None) is None


def test_aliases_explicit_only_never_substring(real):
    rates = real.resolve("gpt-5-codex", "codex")
    assert rates.model_id == "gpt-5" and rates.price_alias == "gpt-5"
    assert real.resolve("gpt-5.1-codex-max", "codex") is None  # D11: no alias -> missing
    assert real.resolve("codex-auto-review", "codex") is None
    assert real.resolve("claude-opus-5-preview", "claude_code") is None
    assert real.resolve("gpt-5", "codex").price_alias is None


def test_one_m_suffix_bills_base_only_for_no_premium_models(real):
    base = real.resolve("claude-opus-5", "claude_code")
    one_m = real.resolve("claude-opus-5[1m]", "claude_code")
    assert one_m.input == base.input and one_m.price_alias == "claude-opus-5"
    assert real.resolve("claude-haiku-4-5[1m]", "claude_code") is None


def test_positive_count_with_absent_rate_is_missing_not_zero(real):
    rates = real.resolve("gpt-5", "codex")
    assert rates.cache_write_1h is None
    assert real.cost({"in_tok": 1, "out_tok": 1, "cache_write_1h_tok": 5}, rates) is None
    assert real.cost({"in_tok": None, "out_tok": 1}, rates) is None
    assert real.cost({"in_tok": 1, "out_tok": 1, "cache_write_1h_tok": 0}, rates) is not None


def test_fingerprint_changes_iff_inputs_change(tmp_path):
    snap = snapshot.latest()
    aliases = tmp_path / "aliases.json"
    aliases.write_bytes(snapshot.ALIASES_PATH.read_bytes())
    a = Pricer(snap, aliases).fingerprint
    assert Pricer(snap, aliases).fingerprint == a
    table = json.loads(aliases.read_text())
    table["openai"]["x"] = "gpt-5"
    aliases.write_text(json.dumps(table))
    assert Pricer(snap, aliases).fingerprint != a
    other = tmp_path / "snap.json"
    other.write_bytes(snap.read_bytes() + b"\n")
    assert Pricer(other, snapshot.ALIASES_PATH).fingerprint != Pricer(snap, snapshot.ALIASES_PATH).fingerprint


def test_snapshot_write_trims_to_catalog_providers(tmp_path):
    catalog = {"anthropic": {"models": {}}, "openai": {"models": {}}, "other": {}}
    path = snapshot.write(catalog, "2026-01-01", tmp_path)
    assert json.loads(path.read_text()).keys() == {"anthropic", "openai"}
    assert snapshot.latest(tmp_path) == path


def test_snapshot_help_neither_fetches_nor_writes(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("--help must not fetch")

    monkeypatch.setattr(snapshot, "fetch", boom)
    monkeypatch.setattr(snapshot.urllib.request, "urlopen", boom)
    before = sorted(snapshot.DATA.iterdir())
    with pytest.raises(SystemExit) as e:
        snapshot.main(["--help"])
    assert e.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert sorted(snapshot.DATA.iterdir()) == before


def test_snapshot_main_passes_date_to_fetch(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(snapshot, "fetch", lambda day=None: calls.append(day) or Path("x.json"))
    snapshot.main(["v9"])
    snapshot.main([])
    assert calls == ["v9", None]
    assert capsys.readouterr().out == "x.json\nx.json\n"
