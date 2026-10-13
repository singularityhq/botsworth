import json

import pytest

from botsworth import pointer
from botsworth.pointer import Bytes, Lines, PointerError, SourceMissing


def test_format_parse_round_trip_lines_bytes_path_hash():
    cases = [
        (
            "claude_code",
            "-Users-x/6d54.jsonl",
            Lines(1182, 1182),
            "message.content.0.text",
            "9b1c0d2e3f405162",
        ),
        ("claude_code", "-Users-x/6d54/tool-results/toolu_01AB.txt", Bytes(0, 618233), None, None),
        ("codex", "sessions/2026/05/18/rollout a#b?.jsonl", Lines(77, 80), "payload.arguments", None),
    ]
    for args in cases:
        uri = pointer.format(*args)
        p = pointer.parse(uri)
        assert (p.provider, p.relpath, p.locator, p.json_path, p.h) == args
        assert pointer.format(p.provider, p.relpath, p.locator, p.json_path, p.h) == uri


def test_parse_normalizes_bracket_index_from_legacy_streams():
    p = pointer.parse("src://omp/a/b.jsonl#L10-L10/message.content[1]")
    assert p.json_path == "message.content.1"


@pytest.mark.parametrize(
    "uri",
    [
        "ptr://claude_code/a#L1-L1",
        "src://codex/a#L0-L1",
        "src://codex/a#L3-L2",
        "src://codex/a",
        "src://codex/a#X1",
    ],
)
def test_bad_grammar_raises(uri):
    with pytest.raises(PointerError):
        pointer.parse(uri)


@pytest.fixture
def root(tmp_path):
    lines = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "héllo world"}]}},
    ]
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "s.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    (tmp_path / "p" / "spill.txt").write_bytes(b"0123456789")
    return {"claude_code": tmp_path}


def test_resolve_json_path_and_part_with_paging(root):
    uri = pointer.format("claude_code", "p/s.jsonl", Lines(2, 2), "message.content")
    whole = pointer.resolve(uri, root, part="0.text")
    assert whole["text"] == "héllo world" and whole["total_bytes"] == 12 and whole["drift"] is False
    page = pointer.resolve(uri, root, part="0.text", offset=3, max_bytes=5)
    assert page["text"] == "llo w" and page["total_bytes"] == 12
    assert whole["locator"] == "L2-L2"


def test_resolve_byte_range(root):
    out = pointer.resolve(pointer.format("claude_code", "p/spill.txt", Bytes(2, 5)), root)
    assert out["text"] == "23456" and out["total_bytes"] == 5


def test_drift_true_iff_hash_present_and_mismatched(root):
    raw = (root["claude_code"] / "p" / "s.jsonl").read_bytes().splitlines(keepends=True)[0]
    good = pointer.format("claude_code", "p/s.jsonl", Lines(1, 1), h=pointer.content_hash(raw))
    assert pointer.resolve(good, root)["drift"] is False
    assert pointer.resolve(pointer.format("claude_code", "p/s.jsonl", Lines(1, 1)), root)["drift"] is False
    (root["claude_code"] / "p" / "s.jsonl").write_text('{"type": "user", "title": "rewritten"}\n')
    out = pointer.resolve(good, root)
    assert out["drift"] is True and "rewritten" in out["text"]


def test_missing_file_and_past_eof_raise_source_missing(root):
    with pytest.raises(SourceMissing):
        pointer.resolve(pointer.format("claude_code", "p/gone.jsonl", Lines(1, 1)), root)
    with pytest.raises(SourceMissing):
        pointer.resolve(pointer.format("claude_code", "p/s.jsonl", Lines(5, 5)), root)
    with pytest.raises(SourceMissing):
        pointer.resolve(pointer.format("claude_code", "p/spill.txt", Bytes(8, 5)), root)


def test_bad_json_path_raises_pointer_error(root):
    with pytest.raises(PointerError):
        pointer.resolve(pointer.format("claude_code", "p/s.jsonl", Lines(1, 1), "message.nope"), root)
