"""Generator for the QF fixture store (TST-1). Run by hand; tests never run it.

    uv run python tests/fixtures/qstore/make.py

It rewrites `streams/*.jsonl` (canonical streams, one bundle per session) and `src/<provider>/...` (the
provider-format lines the streams' `src` pointers address). The output is committed. Every id, path, prompt
and number below is synthetic. Times are written as UTC clock literals; the store zone is Asia/Kolkata.

Layout of a session: `Sess` collects ops, steps and events, and writes one provider line per record a
pointer needs. A pointer is formatted when the session is finished, so its `?h=` hash covers the final
bytes of the line (a tool_use block is appended to its assistant line after the line exists).
Expected numbers are NOT computed here: they are hand-derived in `expected.json`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from botsworth import pointer
from botsworth.model import Bundle, Event, Op, SourceFile, Step
from botsworth.stream import validate, write_bundle

HERE = Path(__file__).resolve().parent
STREAMS = HERE / "streams"
SRC = HERE / "src"
MTIME_NS = 1_790_000_000_000_000_000
MODEL = "synth-model-1"


def at(day: int, hh: int = 0, mm: int = 0, ss: int = 0) -> int:
    """Epoch ms of 2026-10-<day> hh:mm:ss UTC."""
    return int(datetime(2026, 10, day, hh, mm, ss, tzinfo=UTC).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class File:
    """One provider file: a list of (line object, timestamp or None)."""

    def __init__(self, provider: str, rel: str):
        self.provider, self.rel = provider, rel
        self.lines: list[tuple[dict, int | None]] = []

    def add(self, obj: dict, ts: int | None = None) -> int:
        if ts is not None:
            obj.setdefault("timestamp", iso(ts))
        self.lines.append((obj, ts))
        return len(self.lines)

    def raw(self, line: int) -> bytes:
        return (json.dumps(self.lines[line - 1][0], ensure_ascii=False) + "\n").encode()

    def bytes(self) -> bytes:
        return b"".join(self.raw(i) for i in range(1, len(self.lines) + 1))


class Ref:
    """A `src://` pointer formatted late, so its hash covers the final line bytes."""

    def __init__(self, file: File, line: int, path: str | None = None):
        self.file, self.line, self.path = file, line, path

    def __str__(self) -> str:
        h = pointer.content_hash(self.file.raw(self.line))
        loc = pointer.Lines(self.line, self.line)
        return pointer.format(self.file.provider, self.file.rel, loc, self.path, h)


class Sess:
    def __init__(
        self,
        sid: str,
        provider: str,
        project: str,
        start: int,
        *,
        account: str = "anthropic:default",
        rel: str | None = None,
        entrypoint: str | None = None,
        originator: str | None = None,
        first_line: int | None = None,
    ):
        self.sid, self.provider, self.project, self.account = sid, provider, project, account
        self.slug = "-" + project.strip("/").replace("/", "-")
        self.main = File(provider, rel or f"{self.slug}/{sid}.jsonl")
        self.files: dict[str | None, File] = {None: self.main}
        self.ops: list[Op] = []
        self.steps: list[Step] = []
        self.events: list[Event] = []
        self.n_turn: Counter[str | None] = Counter()
        self.n_step: Counter[str] = Counter()
        self.n_event: Counter[str] = Counter()
        self.llm_info: dict[str, tuple[File, int, list, str, str | None]] = {}
        self.session = self.op(
            "session",
            "-",
            None,
            start,
            start,
            Ref(self.main, 1),
            first_line_ms=first_line or start,
            entrypoint=entrypoint,
            originator=originator,
        )

    def op(self, kind: str, key: str, parent: str | None, start: int, end: int, src, **kw) -> str:
        kw.setdefault("id_prov", "natural")
        o = Op(
            op_id=f"{self.provider}/{self.sid}/{kind}/{key}",
            kind=kind,
            provider=self.provider,
            session_id=self.sid,
            src=src,
            parent=parent,
            start_ms=start,
            end_ms=end,
            project=self.project,
            cwd=self.project,
            account=self.account,
            **kw,
        )
        self.ops.append(o)
        return o.op_id

    def file(self, agent: str | None) -> File:
        if agent not in self.files:
            rel = f"{self.slug}/{self.sid}/subagents/agent-{agent}.jsonl"
            self.files[agent] = File(self.provider, rel)
        return self.files[agent]

    def _user(self, text: str, extra: dict | None) -> dict:
        return {
            "type": "user",
            "sessionId": self.sid,
            **(extra or {}),
            "message": {"role": "user", "content": text},
        }

    def turn(
        self,
        label: str,
        start: int,
        end: int,
        kind: str,
        origin: str,
        text: str | None = None,
        *,
        agent: str | None = None,
        parent: str | None = None,
        extra: dict | None = None,
        tail: list[dict] | None = None,
        lines: list[dict] | None = None,
    ) -> str:
        """A turn op plus the provider lines of its boundary record(s); `lines` replaces the Claude shape."""
        f = self.file(agent)
        objs = lines or [self._user(label if text is None else text, extra), *(tail or [])]
        first = f.add(objs[0], start)
        for obj in objs[1:]:
            f.add(obj, start)
        n = self.n_turn[agent]
        self.n_turn[agent] += 1
        return self.op(
            "turn",
            f"{agent or 'main'}#{n}",
            parent or self.session,
            start,
            end,
            Ref(f, first),
            id_prov="derived",
            agent_id=agent,
            label=label,
            turn_kind=kind,
            turn_origin=origin,
            segments=[[start, end]],
            seq=n,
        )

    def _turn_agent(self, turn: str) -> str | None:
        return next(o.agent_id for o in self.ops if o.op_id == turn)

    def step(self, turn: str, op_id: str, verb: str, ref, **kw) -> str:
        n = self.n_step[turn]
        self.n_step[turn] += 1
        self.steps.append(Step(turn_id=turn, ordinal=n, op_id=op_id, verb=verb, src=ref, **kw))
        return f"{turn}#{n}"

    def llm(
        self,
        turn: str,
        key: str,
        start: int,
        end: int,
        cost: float | None,
        *,
        tok: tuple[int, int, int, int] = (100, 20, 0, 0),
        text: str | None = "synthetic reply",
        ttft: int | None = None,
        reasoning: int | None = None,
        dup_of: str | None = None,
        stop: str = "end_turn",
    ) -> str:
        agent = self._turn_agent(turn)
        f = self.file(agent)
        i, o, cr, cw = tok
        content: list[dict] = []
        if text is not None:
            content.append({"type": "text", "text": text})
        if self.provider == "claude_code":
            usage = {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr}
            obj = {
                "type": "assistant",
                "requestId": key,
                "message": {"role": "assistant", "model": MODEL, "content": content, "usage": usage},
            }
            cpath = "message.content"
        elif self.provider == "codex":
            payload = {"type": "message", "role": "assistant", "content": content}
            obj = {"type": "response_item", "payload": payload}
            cpath = "payload.content"
            for b in content:
                b["type"] = "output_text"
        else:
            msg = {"role": "assistant", "responseId": key, "content": content, "model": MODEL}
            msg["usage"] = {"input": i, "output": o}
            obj = {"type": "message", "id": key, "message": msg}
            cpath = "message.content"
        line = f.add(obj, end)
        op_id = self.op(
            "llm_call",
            key,
            turn,
            start,
            end,
            Ref(f, line, cpath + ".0" if text is not None else None),
            agent_id=agent,
            role="main",
            model=MODEL,
            in_tok=i,
            out_tok=o,
            cache_read_tok=cr,
            cache_write_tok=cw,
            reasoning_tok=reasoning,
            tok_prov="present",
            time_prov="present" if self.provider == "omp" else "derived",
            dur_ms=end - start,
            ttft_ms=ttft,
            ttft_prov="present" if ttft is not None else "missing",
            cost_usd=cost,
            cost_basis="reported" if cost is not None else "missing",
            occ_key=key,
            duplicate_of=dup_of,
            stop_reason=stop,
        )
        self.llm_info[op_id] = (f, line, content, turn, agent)
        if text is not None:
            self.step(
                turn,
                op_id,
                "say",
                Ref(f, line, cpath + ".0"),
                summary=text[:120],
                snippet=text[:160],
                start_ms=start,
                dur_ms=end - start,
            )
        return op_id

    def tool(
        self,
        llm: str,
        tid: str,
        name: str,
        start: int,
        end: int,
        verb: str,
        *,
        target: str | None = None,
        command: str | None = None,
        command_key: str | None = None,
        file_path: str | None = None,
        exit_code: int | None = None,
        is_error: bool = False,
        interrupted: bool = False,
        outcome: str = "ok",
        result: str = "ok",
        user_wait: str | None = None,
        time_prov: str = "derived",
        is_async: bool | None = None,
        dup_of: str | None = None,
        input_: dict | None = None,
        step: bool = True,
    ) -> str:
        f, line, content, turn, agent = self.llm_info[llm]
        content.append({"type": "tool_use", "id": tid, "name": name, "input": input_ or {"target": target}})
        idx = len(content) - 1
        rline = f.add(
            {
                "type": "user",
                "sessionId": self.sid,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}
                    ],
                },
            },
            end,
        )
        op_id = self.op(
            "tool_call",
            tid,
            llm,
            start,
            end,
            Ref(f, line, f"message.content.{idx}"),
            agent_id=agent,
            tool_name=name,
            command=command,
            command_norm=command,
            command_key=command_key,
            file_path=file_path,
            exit_code=exit_code,
            exit_code_prov="present" if exit_code is not None else None,
            is_error=is_error,
            interrupted=interrupted,
            result_bytes=len(result.encode()),
            user_wait=user_wait,
            time_prov=time_prov,
            dur_ms=end - start,
            is_async=is_async,
            occ_key=tid,
            duplicate_of=dup_of,
        )
        if step:
            self.step(
                turn,
                op_id,
                verb,
                Ref(f, rline, "message.content.0"),
                tool_name=name,
                target=target,
                outcome=outcome,
                summary=f"{name} {target or ''}".strip()[:120],
                snippet=result[:160],
                start_ms=start,
                dur_ms=end - start,
            )
        return op_id

    def engagement(
        self,
        agent: str,
        ix: int,
        start: int,
        end: int,
        *,
        parent: str,
        caused_by: str | None,
        basis: str,
        agent_type: str,
        arrival: str,
        spawn_depth: int = 0,
    ) -> tuple[str, str]:
        """An engagement op and its agent turn; returns (engagement op_id, agent turn op_id)."""
        eng = self.op(
            "engagement",
            f"{agent}#{ix}",
            parent,
            start,
            end,
            None,
            agent_id=agent,
            engagement_ix=ix,
            agent_type=agent_type,
            agent_name=agent,
            spawn_depth=spawn_depth,
            is_fork=False,
            caused_by=caused_by,
            caused_by_basis=basis,
            segments=[[start, end]],
        )
        t = self.turn(arrival, start, end, "message", "agent", agent=agent, parent=eng)
        next(o for o in self.ops if o.op_id == eng).src = next(o for o in self.ops if o.op_id == t).src
        return eng, t

    def event(self, kind: str, op_id: str, ts: int, ref, basis: str = "present", **fields) -> None:
        n = self.n_event[kind]
        self.n_event[kind] += 1
        self.events.append(
            Event(
                event_id=f"{self.provider}/{self.sid}/event/{kind}#{n}",
                kind=kind,
                session_id=self.sid,
                src=ref,
                ts_ms=ts,
                op_id=op_id,
                account=self.account,
                basis=basis,
                fields=fields,
            )
        )

    def finish(self, end: int, segments: list[list[int]] | None = None, start: int | None = None) -> None:
        root = self.ops[0]
        root.end_ms = end
        if start is not None:
            root.start_ms = start
        root.segments = segments or [[root.start_ms, end]]
        files = [(None, self.main), *[(a, f) for a, f in self.files.items() if a is not None]]
        sfiles = []
        for _, f in files:
            data = f.bytes()
            ts = sorted(t for _, t in f.lines if t is not None)
            sfiles.append(
                SourceFile(
                    provider=self.provider,
                    path=f.rel,
                    size=len(data),
                    mtime_ns=MTIME_NS,
                    sha_first_4k=hashlib.sha256(data[:4096]).hexdigest(),
                    ts_list=ts,
                )
            )
            out = SRC / self.provider / f.rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
        for rec in (*self.ops, *self.steps, *self.events):
            rec.src = str(rec.src)
        bundle = Bundle(
            provider=self.provider,
            bundle_id=self.sid,
            session_id=self.sid,
            ops=self.ops,
            steps=self.steps,
            events=self.events,
            source_files=sfiles,
        )
        validate(bundle)
        write_bundle(bundle, STREAMS)


# ---------------------------------------------------------------------------------------------- sessions

BIZ = "/u/a/bizdemo/src"  # shares its last component "src" with DEC (DM-60)
DEC = "/u/b/demolib/src"


def s2day() -> None:
    """Case 1: $3 at 22:30 IST on 6 Oct and $5 at 00:30 IST on 7 Oct (both 6 Oct in UTC). DM-42, DM-41."""
    s = Sess("s2day", "claude_code", BIZ, at(6, 16, 59, 55))
    t1 = s.turn(
        "refactor the cost rollup for the weekly report", at(6, 16, 59, 55), at(6, 17, 1), "human", "typed"
    )
    s.llm(t1, "req_d1", at(6, 17, 0), at(6, 17, 1), 3.00, tok=(1000, 200, 0, 0))
    t2 = s.turn(
        "now add the daily breakdown to the report", at(6, 18, 59, 55), at(6, 19, 1), "human", "typed"
    )
    s.llm(t2, "req_d2", at(6, 19, 0), at(6, 19, 1), 5.00, tok=(1000, 200, 0, 0))
    s.finish(at(6, 19, 1))


def sauto() -> None:
    """Case 2: one `sdk` message turn, no human ask (DM-25). Only session of project /u/d/helper (DM-61)."""
    s = Sess("sauto", "claude_code", "/u/d/helper", at(6, 3), entrypoint="sdk-cli")
    t = s.turn(
        "classify the open tickets by severity",
        at(6, 3),
        at(6, 3, 0, 25),
        "message",
        "sdk",
        extra={"promptSource": "sdk", "entrypoint": "sdk-cli"},
    )
    s.llm(t, "req_a1", at(6, 3, 0, 5), at(6, 3, 0, 25), 0.50)
    s.finish(at(6, 3, 0, 25))


def sunpriced() -> None:
    """Case 3: three calls costing 1.00, null, 2.00 (QRY-11)."""
    s = Sess("sunpriced", "claude_code", BIZ, at(6, 5))
    t = s.turn("total the invoices for the third quarter report", at(6, 5), at(6, 5, 2, 35), "human", "typed")
    s.llm(t, "req_u1", at(6, 5, 0, 5), at(6, 5, 0, 35), 1.00)
    s.llm(t, "req_u2", at(6, 5, 1, 5), at(6, 5, 1, 35), None)
    s.llm(t, "req_u3", at(6, 5, 2, 5), at(6, 5, 2, 35), 2.00)
    s.finish(at(6, 5, 2, 35))


def szero() -> None:
    """Case 4: a Codex session with turns and no llm_call. Only session of DEC (n_sessions 0, DM-61)."""
    s = Sess(
        "szero",
        "codex",
        DEC,
        at(6, 6),
        account="openai:plus",
        entrypoint="vscode",
        originator="codex_vscode",
        rel="sessions/2026/10/06/rollout-szero.jsonl",
    )
    for label, start, end, kind, origin, user in (
        (
            "inspect the pricing table layout",
            at(6, 6),
            at(6, 6, 0, 30),
            "human",
            "typed",
            "inspect the pricing table layout",
        ),
        ("(no text)", at(6, 6, 2), at(6, 6, 2), "harness", "empty", ""),
    ):
        line = {"type": "event_msg", "payload": {"type": "user_message", "message": user}}
        s.turn(label, start, end, kind, origin, lines=[line])
    s.finish(at(6, 6, 2))


def sagents() -> None:
    """Case 6: DM-45 attribution, a `+cycle` reply, a spawn call with two engagements, a nested agent, an
    engagement with no cause. Timeline in expected.json."""
    s = Sess("sagents", "claude_code", "/u/e/agentsdemo", at(8, 9))
    t1 = s.turn("have the reviewer check the migration plan", at(8, 9), at(8, 9, 0, 17), "human", "typed")
    c1 = s.llm(t1, "req_c1", at(8, 9, 0, 5), at(8, 9, 0, 15), 0.10, text=None, stop="tool_use")
    sp_a = s.tool(
        c1,
        "toolu_spawnA",
        "Agent",
        at(8, 9, 0, 15),
        at(8, 9, 0, 17),
        "spawn",
        target="reviewer",
        is_async=True,
    )
    sp_b = s.tool(
        c1, "toolu_spawnB", "Agent", at(8, 9, 0, 15), at(8, 9, 0, 17), "spawn", target="worker", is_async=True
    )
    # A#0 ($2.00) with a nested agent N (depth 2, $0.50)
    ea0, ta0 = s.engagement(
        "ra",
        0,
        at(8, 9, 1),
        at(8, 9, 5, 30),
        parent=sp_a,
        caused_by=sp_a,
        basis="id",
        agent_type="reviewer",
        arrival="review the migration plan and report",
    )
    a0 = s.llm(ta0, "req_ra0", at(8, 9, 1), at(8, 9, 4), 2.00, text=None, stop="tool_use")
    sp_n = s.tool(
        a0, "toolu_spawnN", "Agent", at(8, 9, 4), at(8, 9, 4, 30), "spawn", target="checker", is_async=True
    )
    en, tn = s.engagement(
        "rn",
        0,
        at(8, 9, 4, 30),
        at(8, 9, 5, 30),
        parent=sp_n,
        caused_by=sp_n,
        basis="id",
        agent_type="checker",
        arrival="check the table list",
        spawn_depth=1,
    )
    s.llm(tn, "req_rn0", at(8, 9, 4, 30), at(8, 9, 5, 30), 0.50)
    # B#0 (10 min, $0.40): the first engagement of the spawn call that B#1 reuses an hour later
    eb0, tb0 = s.engagement(
        "rb",
        0,
        at(8, 9, 2, 30),
        at(8, 9, 12, 30),
        parent=sp_b,
        caused_by=sp_b,
        basis="id",
        agent_type="worker",
        arrival="load the staging data",
    )
    s.llm(tb0, "req_rb0", at(8, 9, 2, 30), at(8, 9, 12, 30), 0.40)
    # C#0: no cause found (caused_by NULL, basis none), parent is the session: unattributed ($0.30)
    ec0, tc0 = s.engagement(
        "rc",
        0,
        at(8, 9, 3),
        at(8, 9, 3, 30),
        parent=s.session,
        caused_by=None,
        basis="none",
        agent_type="scout",
        arrival="scan the open tickets",
    )
    s.llm(tc0, "req_rc0", at(8, 9, 3), at(8, 9, 3, 30), 0.30)
    # T2: SendMessage to A causes A#1 ($1.00); the cycle rule parents it under A's spawn call
    t2 = s.turn("ask the reviewer for the verdict", at(8, 9, 50), at(8, 10, 7, 40), "human", "typed")
    c2 = s.llm(t2, "req_c2", at(8, 9, 50, 5), at(8, 9, 50, 15), 0.10, text=None, stop="tool_use")
    sp_s = s.tool(
        c2,
        "toolu_sendA",
        "SendMessage",
        at(8, 9, 50, 15),
        at(8, 9, 50, 17),
        "other",
        target="ra",
        is_async=True,
    )
    ea1, ta1 = s.engagement(
        "ra",
        1,
        at(8, 10),
        at(8, 10, 1, 15),
        parent=sp_a,
        caused_by=sp_s,
        basis="content_hash+cycle",
        agent_type="reviewer",
        arrival="give the verdict now",
    )
    s.llm(ta1, "req_ra1", at(8, 10), at(8, 10, 1, 15), 1.00)
    # B#1 (5 min, $0.20), one hour after B#0, caused by the same spawn call
    eb1, tb1 = s.engagement(
        "rb",
        1,
        at(8, 10, 2, 30),
        at(8, 10, 7, 30),
        parent=sp_b,
        caused_by=sp_b,
        basis="id",
        agent_type="worker",
        arrival="load the production data",
    )
    s.llm(tb1, "req_rb1", at(8, 10, 2, 30), at(8, 10, 7, 30), 0.20)
    s.llm(t2, "req_c2b", at(8, 10, 7, 30), at(8, 10, 7, 40), 0.10)
    s.finish(at(8, 10, 7, 40))


def sharness() -> None:
    """Case 7: /clear, [Request interrupted by user] and an empty turn between asks (DM-20, DM-24)."""
    s = Sess("sharness", "claude_code", "/u/f/harnessproj", at(8, 12))
    t0 = s.turn(
        "outline the migration steps for the billing tables", at(8, 12), at(8, 12, 0, 35), "human", "typed"
    )
    s.llm(t0, "req_h1", at(8, 12, 0, 5), at(8, 12, 0, 35), 0.10)
    clear = (
        "<command-name>/clear</command-name>\n<command-message>clear</command-message>\n"
        "<command-args></command-args>"
    )
    s.turn(
        "/clear",
        at(8, 12, 1),
        at(8, 12, 1),
        "harness",
        "builtin:clear",
        clear,
        tail=[
            {
                "type": "system",
                "subtype": "local_command",
                "content": "<local-command-stdout></local-command-stdout>",
            }
        ],
    )
    t2 = s.turn(
        "summarize what changed in the schema this week", at(8, 12, 3), at(8, 12, 3, 35), "human", "typed"
    )
    s.llm(t2, "req_h2", at(8, 12, 3, 5), at(8, 12, 3, 35), 0.20)
    s.turn("[Request interrupted by user]", at(8, 12, 5), at(8, 12, 5), "harness", "interrupt")
    s.turn("(no text)", at(8, 12, 6, 30), at(8, 12, 6, 30), "harness", "empty", "")
    t5 = s.turn("continue with the second table now", at(8, 12, 8), at(8, 12, 8, 35), "human", "typed")
    s.llm(t5, "req_h3", at(8, 12, 8, 5), at(8, 12, 8, 35), 0.30)
    s.finish(at(8, 12, 8, 35))


def stools() -> None:
    """Cases 9, 12 (owner), 13, 15. Day 9, 05:00Z."""
    s = Sess("stools", "claude_code", BIZ, at(9, 5))
    t1 = s.turn(
        "run the tests and fix any failures in the parser module", at(9, 5), at(9, 5, 0, 55), "human", "typed"
    )
    # case 13: one call (700 + 300 = 1,000 tokens) issues two `uv run pytest` Bash calls and one Read
    l1 = s.llm(
        t1, "req_t1", at(9, 5, 0, 5), at(9, 5, 0, 25), 0.40, tok=(700, 300, 0, 0), text=None, stop="tool_use"
    )
    # case 9: a Bash call with exit code 1
    s.tool(
        l1,
        "toolu_pt1",
        "Bash",
        at(9, 5, 0, 25),
        at(9, 5, 0, 55),
        "run",
        target="uv run pytest",
        command="uv run pytest tests/unit -q",
        command_key="uv run pytest",
        exit_code=1,
        outcome="exit:1",
        result="FAILED tests/unit/test_x.py::test_y\n1 failed",
    )
    s.tool(
        l1,
        "toolu_pt2",
        "Bash",
        at(9, 5, 0, 25),
        at(9, 5, 0, 45),
        "run",
        target="uv run pytest",
        command="uv run pytest tests/unit/test_z.py -q",
        command_key="uv run pytest",
        exit_code=0,
        result="3 passed",
    )
    s.tool(
        l1,
        "toolu_rd1",
        "Read",
        at(9, 5, 0, 25),
        at(9, 5, 0, 26),
        "read",
        target="src/parser.py",
        file_path="/u/a/bizdemo/src/parser.py",
        result="synthetic file body",
    )
    t2 = s.turn(
        "check the interrupted run and the failing edit", at(9, 5, 1), at(9, 5, 1, 35), "human", "typed"
    )
    chain_l2(s, t2)
    t3 = s.turn("show the full log of the last run", at(9, 5, 2), at(9, 5, 2, 16), "human", "typed")
    l3 = s.llm(
        t3, "req_t3", at(9, 5, 2, 5), at(9, 5, 2, 15), 0.20, tok=(200, 50, 0, 0), text=None, stop="tool_use"
    )
    # case 15: a tool result of 11,264 bytes
    big = "".join(f"synthetic output line {i:04d} " + "x" * 33 + "\n" for i in range(400))[:11264]
    s.tool(
        l3,
        "toolu_big",
        "Read",
        at(9, 5, 2, 15),
        at(9, 5, 2, 16),
        "read",
        target="run.log",
        file_path="/u/a/bizdemo/src/run.log",
        result=big,
    )
    s.finish(at(9, 5, 2, 16))
    assert len(big.encode()) == 11264


def chain_l2(s: Sess, turn: str, dup: bool = False) -> str:
    """The call of case 9 (interrupted tool, is_error tool). `dup` builds the fork's copy (case 12)."""

    def dk(kind: str, key: str) -> str | None:
        return f"claude_code/stools/{kind}/{key}" if dup else None

    l2 = s.llm(
        turn,
        "req_t2",
        at(9, 5, 1, 5),
        at(9, 5, 1, 25),
        0.30,
        tok=(500, 100, 0, 0),
        text=None,
        stop="tool_use",
        dup_of=dk("llm_call", "req_t2"),
    )
    # case 9: an interrupted Bash call and an is_error Edit call
    s.tool(
        l2,
        "toolu_int1",
        "Bash",
        at(9, 5, 1, 25),
        at(9, 5, 1, 35),
        "run",
        target="sleep 600",
        command="sleep 600",
        command_key="sleep",
        interrupted=True,
        outcome="interrupted",
        result="[Request interrupted by user for tool use]",
        dup_of=dk("tool_call", "toolu_int1"),
        step=not dup,
    )
    s.tool(
        l2,
        "toolu_err1",
        "Edit",
        at(9, 5, 1, 25),
        at(9, 5, 1, 26),
        "edit",
        target="src/parser.py",
        file_path="/u/a/bizdemo/src/parser.py",
        is_error=True,
        outcome="error",
        result="String to replace not found in file",
        dup_of=dk("tool_call", "toolu_err1"),
        step=not dup,
    )
    return l2


def sfork() -> None:
    """Case 12: a fork whose copied prefix repeats the call `req_t2` and its tools; the store marks them
    duplicates of stools'. The fork's first line is later than stools', so stools owns them."""
    s = Sess("sfork", "claude_code", BIZ, at(9, 5, 1), first_line=at(9, 5, 30))
    t0 = s.turn(
        "check the interrupted run and the failing edit", at(9, 5, 1), at(9, 5, 1, 35), "human", "typed"
    )
    chain_l2(s, t0, dup=True)
    t1 = s.turn(
        "list the remaining parser failures in order", at(9, 5, 30), at(9, 5, 30, 25), "human", "typed"
    )
    s.llm(t1, "req_f1", at(9, 5, 30, 5), at(9, 5, 30, 25), 0.20)
    s.finish(at(9, 5, 30, 25), segments=[[at(9, 5, 1), at(9, 5, 1, 35)], [at(9, 5, 30), at(9, 5, 30, 25)]])


def sskills() -> None:
    """Case 10: slash skill turn (event, no step), a turn with two Skill steps, a turn with one."""
    s = Sess("sskills", "claude_code", "/u/g/skillproj", at(9, 8))
    expanded = {
        "type": "user",
        "isMeta": True,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "Base directory for this skill: /u/g/skills/bootstrap"}],
        },
    }
    cmd = (
        "<command-name>/mad:bootstrap</command-name>\n<command-message>mad:bootstrap</command-message>\n"
        "<command-args>ARGS</command-args>"
    )
    t1 = s.turn("/mad:bootstrap ARGS", at(9, 8), at(9, 8, 0, 25), "human", "slash", cmd, tail=[expanded])
    s.llm(t1, "req_k1", at(9, 8, 0, 5), at(9, 8, 0, 25), 0.50, text=None)
    s.event(
        "skill_invocation", t1, at(9, 8), s.ops[1].src, skill="mad:bootstrap", args_head="ARGS", via="slash"
    )
    t2 = s.turn(
        "apply the alpha and beta skills to the draft", at(9, 8, 5), at(9, 8, 5, 22), "human", "typed"
    )
    l2 = s.llm(t2, "req_k2", at(9, 8, 5, 5), at(9, 8, 5, 20), 0.60, text=None, stop="tool_use")
    for tid, name, off in (("toolu_sk1", "demo:alpha", 0), ("toolu_sk2", "demo:beta", 1)):
        op = s.tool(
            l2,
            tid,
            "Skill",
            at(9, 8, 5, 20),
            at(9, 8, 5, 21 + off),
            "skill",
            target=name,
            input_={"skill": name},
            result=f"Launching skill: {name}",
        )
        s.event(
            "skill_invocation",
            op,
            at(9, 8, 5, 20),
            next(o for o in s.ops if o.op_id == op).src,
            skill=name,
            args_head="",
            via="tool",
            step_id=f"{t2}#{off}",
        )
    t3 = s.turn("load alpha again for the follow up edit", at(9, 8, 10), at(9, 8, 10, 22), "human", "typed")
    l3 = s.llm(t3, "req_k3", at(9, 8, 10, 5), at(9, 8, 10, 20), 0.20, text=None, stop="tool_use")
    op = s.tool(
        l3,
        "toolu_sk3",
        "Skill",
        at(9, 8, 10, 20),
        at(9, 8, 10, 21),
        "skill",
        target="demo:alpha",
        input_={"skill": "demo:alpha"},
        result="Launching skill: demo:alpha",
    )
    s.event(
        "skill_invocation",
        op,
        at(9, 8, 10, 20),
        next(o for o in s.ops if o.op_id == op).src,
        skill="demo:alpha",
        args_head="",
        via="tool",
        step_id=f"{t3}#0",
    )
    s.finish(at(9, 8, 10, 22))


def slow() -> None:
    """Cases 11 and 16 (CLI-20): walls 60 and 30 min in /u/h/slowproj/app on day 7."""
    proj = "/u/h/slowproj/app"
    s = Sess("sslow1", "claude_code", proj, at(7, 4))
    t = s.turn("benchmark the slow ingest path and report", at(7, 4), at(7, 5), "human", "typed")
    l1 = s.llm(t, "req_w1", at(7, 4, 0, 5), at(7, 4, 2), 0.50, text=None, stop="tool_use")
    # case 11: a derived 40-min Bash duration; a queue-operation record 10 min in leaves a 30-min silence
    s.main.add({"type": "queue-operation", "operation": "enqueue", "sessionId": "sslow1"}, at(7, 4, 12))
    s.tool(
        l1,
        "toolu_slowbash",
        "Bash",
        at(7, 4, 2),
        at(7, 4, 42),
        "run",
        target="make bench",
        command="make bench",
        command_key="make bench",
        exit_code=0,
        result="bench done",
    )
    l2 = s.llm(t, "req_w2", at(7, 4, 42), at(7, 4, 45), 0.50, text=None, stop="tool_use")
    # case 11: an AskUserQuestion call of 15 min that waits on the user
    s.tool(
        l2,
        "toolu_ask1",
        "AskUserQuestion",
        at(7, 4, 45),
        at(7, 5),
        "ask-user",
        target="which build to keep",
        user_wait="ask",
        result="the first build",
    )
    s.finish(at(7, 5))
    s = Sess("sslow2", "claude_code", proj, at(7, 6))
    t1 = s.turn("check the report totals after the rebuild", at(7, 6), at(7, 6, 2), "human", "typed")
    s.llm(t1, "req_x1", at(7, 6, 0, 5), at(7, 6, 2), 0.25)
    t2 = s.turn("thanks, now draft the summary for review", at(7, 6, 27), at(7, 6, 30), "human", "typed")
    s.llm(t2, "req_x2", at(7, 6, 27, 5), at(7, 6, 30), 0.25)
    s.finish(at(7, 6, 30))


def sstretch() -> None:
    """Case 8: idle stretches across a human ask, before a limit turn and a peer turn; agents stretches
    around an agent call that waits for a permission (D54)."""
    s = Sess("sstretch", "claude_code", "/u/i/idleproj", at(10, 2))
    t1 = s.turn("draft the capacity plan for the next quarter", at(10, 2), at(10, 2, 1), "human", "typed")
    s.llm(t1, "req_i1", at(10, 2, 0, 5), at(10, 2, 1), 0.10)
    t2 = s.turn(
        "estimate the staffing needs per team and region", at(10, 2, 26), at(10, 2, 49), "human", "typed"
    )
    s.llm(t2, "req_i2", at(10, 2, 48), at(10, 2, 49), 0.10)
    limit = "I hit my usage limit earlier, please resume the staffing estimate"
    t3 = s.turn(limit, at(10, 3, 51), at(10, 3, 52, 5), "human", "typed")
    s.llm(t3, "req_i3", at(10, 3, 51, 5), at(10, 3, 52, 5), 0.10)
    s.event(
        "limit_hit",
        t3,
        at(10, 3, 51),
        s.ops[-2].src,
        basis="derived",
        window="session",
        resets_at_ms=at(10, 7),
        tz="Asia/Kolkata",
    )
    body = "ack: the capacity numbers look right to the team"
    peer = f'<teammate-message teammate_id="peer-1">{body}</teammate-message>'
    t4 = s.turn(
        "ack: the capacity numbers look right to the team",
        at(10, 4, 4),
        at(10, 4, 5, 5),
        "message",
        "peer",
        peer,
    )
    s.llm(t4, "req_i4", at(10, 4, 4, 5), at(10, 4, 5, 5), 0.10)
    t5 = s.turn("run the forecast agent over all regions", at(10, 4, 6), at(10, 4, 48, 5), "human", "typed")
    l5 = s.llm(t5, "req_i5", at(10, 4, 6, 5), at(10, 4, 7, 5), 0.10, text=None, stop="tool_use")
    sp = s.tool(
        l5,
        "toolu_spawnD",
        "Agent",
        at(10, 4, 7, 5),
        at(10, 4, 7, 7),
        "spawn",
        target="forecaster",
        is_async=True,
    )
    _, td = s.engagement(
        "rd",
        0,
        at(10, 4, 7, 35),
        at(10, 4, 47, 35),
        parent=sp,
        caused_by=sp,
        basis="id",
        agent_type="forecaster",
        arrival="forecast every region",
    )
    # D54: the agent asks for a permission at 04:20:00; the user answers 4 min later and the agent goes on
    ld = s.llm(td, "req_rd0", at(10, 4, 7, 35), at(10, 4, 20), 0.40, text=None, stop="tool_use")
    s.tool(
        ld,
        "toolu_agperm",
        "Bash",
        at(10, 4, 20),
        at(10, 4, 24),
        "run",
        target="make forecast",
        command="make forecast",
        command_key="make forecast",
        exit_code=0,
        user_wait="permission",
        result="forecast done",
    )
    s.llm(td, "req_rd1", at(10, 4, 24), at(10, 4, 47, 35), 0.40)
    s.llm(t5, "req_i6", at(10, 4, 47, 35), at(10, 4, 48, 5), 0.10)
    s.finish(at(10, 4, 48, 5))


def somp() -> None:
    """Case 14: an omp session with `ttft_ms` present."""
    s = Sess("somp", "omp", "/u/j/ompproj", at(10, 10), account="openai:omp")
    user = {
        "type": "message",
        "id": "m0",
        "message": {
            "role": "user",
            "attribution": "user",
            "content": [{"type": "text", "text": "profile the startup path of the daemon"}],
        },
    }
    t = s.turn(
        "profile the startup path of the daemon",
        at(10, 10),
        at(10, 10, 0, 26),
        "human",
        "typed",
        lines=[user],
    )
    s.llm(t, "resp_o1", at(10, 10, 0, 5), at(10, 10, 0, 9), 0.20, tok=(300, 200, 0, 0), ttft=800)
    s.llm(t, "resp_o2", at(10, 10, 0, 20), at(10, 10, 0, 26), 0.30, tok=(400, 300, 0, 0), ttft=1200)
    s.finish(at(10, 10, 0, 26))


def scodex() -> None:
    """Case 14: a Codex session whose call carries two reasoning items (DET-63): one with a summary, one with
    only encrypted content."""
    s = Sess(
        "scodex",
        "codex",
        "/u/k/codexproj",
        at(10, 11),
        account="openai:plus",
        entrypoint="vscode",
        originator="codex_vscode",
        rel="sessions/2026/10/10/rollout-scodex.jsonl",
    )
    user = {
        "type": "event_msg",
        "payload": {"type": "user_message", "message": "explain why the cache misses spike after deploy"},
    }
    t = s.turn(
        "explain why the cache misses spike after deploy",
        at(10, 11),
        at(10, 11, 0, 25),
        "human",
        "typed",
        lines=[user],
    )
    r1 = s.main.add(
        {
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Weighing cache eviction after the deploy."}],
                "encrypted_content": "enc-synthetic-0001",
            },
        },
        at(10, 11, 0, 4),
    )
    r2 = s.main.add(
        {
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": [], "encrypted_content": "enc-synthetic-0002"},
        },
        at(10, 11, 0, 4),
    )
    call = s.llm(
        t, "resp_cx1", at(10, 11, 0, 5), at(10, 11, 0, 25), 0.15, tok=(500, 150, 200, 0), reasoning=128
    )
    s.steps.clear()  # rebuild the steps in file order: think, think, answer
    s.n_step[t] = 0
    s.step(
        t,
        call,
        "think",
        Ref(s.main, r1, "payload"),
        summary="Weighing cache eviction after the deploy.",
        snippet="Weighing cache eviction after the deploy.",
        start_ms=at(10, 11, 0, 5),
        dur_ms=0,
    )
    s.step(t, call, "think", Ref(s.main, r2, "payload"), start_ms=at(10, 11, 0, 5), dur_ms=0)
    s.step(
        t,
        call,
        "answer",
        s.ops[-1].src,
        summary="synthetic reply",
        snippet="synthetic reply",
        start_ms=at(10, 11, 0, 5),
        dur_ms=20000,
    )
    s.finish(at(10, 11, 0, 25))


def main() -> None:
    for d in (STREAMS, SRC):
        shutil.rmtree(d, ignore_errors=True)
    STREAMS.mkdir(parents=True)
    for build in (
        s2day,
        sauto,
        sunpriced,
        szero,
        sagents,
        sharness,
        stools,
        sfork,
        sskills,
        slow,
        sstretch,
        somp,
        scodex,
    ):
        build()


if __name__ == "__main__":
    main()
