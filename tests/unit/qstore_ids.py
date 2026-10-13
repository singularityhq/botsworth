"""Ids of the QF fixture store (tests/fixtures/qstore) that tests reference by name.

Session keys are `<provider>/<session_id>` (`sessions.bundle`). Op ids follow design §4.3. Turn ids are
`main#<n>` in time order (harness turns count: `main#1` of `sharness` is `/clear`). Nothing here is read from
the store: the names come from `tests/fixtures/qstore/make.py`.
"""

from __future__ import annotations

CC = "claude_code"


def op(session: str, kind: str, key: str, provider: str = CC) -> str:
    return f"{provider}/{session}/{kind}/{key}"


# session keys (case numbers in the comments are the QF contract's)
S2DAY = f"{CC}/s2day"  # 1
SAUTO = f"{CC}/sauto"  # 2
SUNPRICED = f"{CC}/sunpriced"  # 3
SZERO = "codex/szero"  # 4
SAGENTS = f"{CC}/sagents"  # 6
SHARNESS = f"{CC}/sharness"  # 7
SSTRETCH = f"{CC}/sstretch"  # 8
STOOLS = f"{CC}/stools"  # 9, 12, 13, 15
SSKILLS = f"{CC}/sskills"  # 10
SSLOW1 = f"{CC}/sslow1"  # 11, 16
SSLOW2 = f"{CC}/sslow2"  # 16
SFORK = f"{CC}/sfork"  # 12
SOMP = "omp/somp"  # 14
SCODEX = "codex/scodex"  # 14
SESSIONS = (
    S2DAY, SAUTO, SUNPRICED, SZERO, SAGENTS, SHARNESS, SSTRETCH, STOOLS, SSKILLS, SSLOW1, SSLOW2, SFORK,
    SOMP, SCODEX,
)  # fmt: skip


def turn(session_key: str, n: int) -> str:
    """Main turn `n` (0-based, time order) of a session key."""
    provider, sid = session_key.split("/")
    return op(sid, "turn", f"main#{n}", provider)


# case 6: engagements and the spawn / message calls that caused them
SAGENTS_T1, SAGENTS_T2 = turn(SAGENTS, 0), turn(SAGENTS, 1)
A0 = op("sagents", "engagement", "ra#0")
A1 = op("sagents", "engagement", "ra#1")  # SendMessage reply, `+cycle`, parent = A's spawn call
NESTED = op("sagents", "engagement", "rn#0")  # runs inside A0 (agent depth 2)
B0 = op("sagents", "engagement", "rb#0")
B1 = op("sagents", "engagement", "rb#1")  # same spawn call as B0, one hour later
C0 = op("sagents", "engagement", "rc#0")  # caused_by NULL: unattributed
SPAWN_A = op("sagents", "tool_call", "toolu_spawnA")
SPAWN_B = op("sagents", "tool_call", "toolu_spawnB")
SPAWN_N = op("sagents", "tool_call", "toolu_spawnN")
SEND_A = op("sagents", "tool_call", "toolu_sendA")
SAGENTS_CALLS = {  # request id -> op_id of the llm_call
    k: op("sagents", "llm_call", k)
    for k in ("req_c1", "req_ra0", "req_rn0", "req_rb0", "req_rc0", "req_c2", "req_ra1", "req_rb1", "req_c2b")
}

# cases 9, 13, 15: tool calls of stools
PYTEST_FAIL = op("stools", "tool_call", "toolu_pt1")  # Bash, exit_code 1
PYTEST_OK = op("stools", "tool_call", "toolu_pt2")
READ_SMALL = op("stools", "tool_call", "toolu_rd1")
TOOL_INTERRUPTED = op("stools", "tool_call", "toolu_int1")
TOOL_ERROR = op("stools", "tool_call", "toolu_err1")  # Edit, is_error
TOOL_BIG = op("stools", "tool_call", "toolu_big")  # result 11,264 bytes
CALL_THREE_TOOLS = op("stools", "llm_call", "req_t1")  # 1,000 tokens, two pytest Bash calls and one Read
CALL_COPIED = op("stools", "llm_call", "req_t2")  # copied by sfork
FORK_COPIES = (  # the fork's duplicate copies of CALL_COPIED, TOOL_INTERRUPTED and TOOL_ERROR
    op("sfork", "llm_call", "req_t2"),
    op("sfork", "tool_call", "toolu_int1"),
    op("sfork", "tool_call", "toolu_err1"),
)

# case 10: skills
SKILL_SLASH_TURN = turn(SSKILLS, 0)  # /mad:bootstrap ARGS
SKILL_TWO_TURN = turn(SSKILLS, 1)  # loads demo:alpha and demo:beta
SKILL_ONE_TURN = turn(SSKILLS, 2)  # loads demo:alpha

# case 11 and 16
SLOW_BASH = op("sslow1", "tool_call", "toolu_slowbash")  # 40 min derived, 30 min silence
ASK_USER = op("sslow1", "tool_call", "toolu_ask1")  # 15 min, waits_on_user
SLOW_PROJECT = "/u/h/slowproj/app"

# case 14
OMP_CALLS = (op("somp", "llm_call", "resp_o1", "omp"), op("somp", "llm_call", "resp_o2", "omp"))
CODEX_CALL = op("scodex", "llm_call", "resp_cx1", "codex")
