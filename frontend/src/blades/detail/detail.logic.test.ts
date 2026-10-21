import { describe, expect, it } from 'vitest'
import type { AgentDetail, CallDetail, GapRow, NextTurn } from '../../api/types'
import { call } from '../turn/testRows'
import {
  callsLedger, charCount, clampOf, costCaption, countLines, fmtBytes, gapBasis, gapCaption, imageLine, limitInference, outputView,
  planCall, prettyJson, senderOf, share, speedTokS, splitImages, splitLines, startsTurnLine, stepKind, thinkingState, tokenLine, toolStatus,
  ttftText,
} from './detail.logic'

const tok = (o: Partial<ReturnType<typeof call>['tok']> = {}) => ({ in: 10, cache_read: 20, cache_write: 30, out: 40, reasoning: null, total: 100, ...o })
const t = (o: Partial<Parameters<typeof toolStatus>[0]> = {}) => ({ exit_code: null, interrupted: false, status: null, failed: false, is_error: false, ...o })

describe('the tool status (DET-20, D38 L8)', () => {
  it('a non-zero exit code is a failure that reads "exit N"', () => {
    expect(toolStatus(t({ exit_code: 2, failed: true }))).toEqual({ text: 'exit 2', failed: true })
  })
  it('exit 0 is a plain "exit 0", not a failure', () => {
    expect(toolStatus(t({ exit_code: 0 }))).toEqual({ text: 'exit 0', failed: false })
  })
  it('the exit code comes first: an exit code beats an interrupt flag and an outcome', () => {
    expect(toolStatus(t({ exit_code: 3, interrupted: true, status: 'error', is_error: true }))?.text).toBe('exit 3')
  })
  it('then interrupted, then the outcome', () => {
    expect(toolStatus(t({ interrupted: true, is_error: true }))?.text).toBe('interrupted')
    expect(toolStatus(t({ status: 'exit:127', failed: true }))?.text).toBe('exit 127')
    expect(toolStatus(t({ status: 'error', is_error: true }))?.text).toBe('error')
  })
  it('an outcome of exit:0 reads "exit 0"', () => {
    expect(toolStatus(t({ status: 'exit:0' }))).toEqual({ text: 'exit 0', failed: false })
  })
  it('a tool with no known status shows nothing, never "ok"', () => {
    expect(toolStatus(t())).toBeNull()
    expect(toolStatus(t({ status: 'ok' }))).toBeNull()
  })
})

describe('the token line (DET-12)', () => {
  it('lists in, cache read, cache write and out, and leaves null figures out', () => {
    expect(tokenLine(tok()).map((p) => p.label)).toEqual(['in', 'cache read', 'cache write', 'out'])
    expect(tokenLine(tok({ cache_read: null, cache_write: null })).map((p) => p.label)).toEqual(['in', 'out'])
  })
  it('adds reasoning when the call has it (Codex), and keeps a measured zero', () => {
    const line = tokenLine(tok({ reasoning: 512, cache_write: 0 }))
    expect(line.map((p) => p.label)).toEqual(['in', 'cache read', 'cache write', 'out', 'reasoning'])
    expect(line.find((p) => p.label === 'cache write')!.value).toBe(0)
  })
})

describe('the percent captions (DET-10, DET-30, DET-40)', () => {
  it('rounds to a whole percent and prints <1% below 1 %', () => {
    expect(share(13, 100)).toBe('13%')
    expect(share(0.4, 100)).toBe('<1%')
    expect(costCaption(0.13, 1)).toBe("13% of the turn's cost")
    expect(costCaption(0.004, 1)).toBe("<1% of the turn's cost")
  })
  it("is omitted at a turn cost of 0, and when either figure is not known", () => {
    expect(costCaption(1, 0)).toBeNull()
    expect(costCaption(1, null)).toBeNull()
    expect(costCaption(null, 5)).toBeNull()
  })
  it("a zoom says the range's cost (DET-30)", () => {
    expect(costCaption(2, 4, 'range')).toBe("50% of the range's cost")
  })
  it("a gap inside the turn is a share of the turn's wall time", () => {
    const g: Pick<GapRow, 'state' | 'substate' | 'turn' | 'dur_ms'> = { state: 'none', substate: 'none', turn: 'x/turn/1', dur_ms: 30_000 }
    expect(gapBasis(g)).toBe('turn')
    expect(gapCaption(g, 120_000, 3_600_000)).toBe("25% of the turn's wall time")
  })
  it("a gap between turns or before the first message is a share of the session's wall time", () => {
    expect(gapCaption({ state: 'you', substate: 'reply', turn: null, dur_ms: 360_000 }, 120_000, 3_600_000)).toBe("10% of the session's wall time")
    expect(gapCaption({ state: 'none', substate: 'before_first', turn: 'x/turn/1', dur_ms: 360_000 }, 120_000, 3_600_000)).toBe("10% of the session's wall time")
  })
})

describe('model call strip (DET-11, DET-14)', () => {
  it('tok/s needs output tokens and model time above 0; the server figure stands when it has one', () => {
    expect(speedTokS({ speed_tok_s: 123.4, tok: tok(), dur_ms: 1000 })).toBe(123)
    expect(speedTokS({ speed_tok_s: null, tok: tok({ out: 100 }), dur_ms: 2000 })).toBe(50)
    expect(speedTokS({ speed_tok_s: null, tok: tok({ out: 0 }), dur_ms: 2000 })).toBeNull()
    expect(speedTokS({ speed_tok_s: null, tok: tok({ out: 10 }), dur_ms: 0 })).toBeNull()
    expect(speedTokS({ speed_tok_s: null, tok: tok({ out: null }), dur_ms: 1000 })).toBeNull()
  })
  it('first token in seconds with one decimal below 10 s', () => {
    expect(ttftText(1234)).toBe('first token 1.2s')
    expect(ttftText(12_000)).toBe('first token 12s')
  })
  it('sections keep the content order, and the i-th text block takes the i-th say/answer verb', () => {
    const c = {
      is_final: true,
      steps: [
        { step_id: 'a', verb: 'think', summary: '', snippet: '' },
        { step_id: 'b', verb: 'say', summary: '', snippet: '' },
        { step_id: 'c', verb: 'answer', summary: '', snippet: '' },
      ],
      sections: [
        { part: 'thinking', src: 's1', snippet: '', bytes: 1, row: null },
        { part: 'text', src: 's2', snippet: '', bytes: 1, row: null },
        { part: 'tool_use', src: 's3', snippet: '', bytes: 1, row: { type: 'tool', id: 't' } },
        { part: 'text', src: 's4', snippet: '', bytes: 1, row: null },
      ],
    } as Pick<CallDetail, 'sections' | 'steps' | 'is_final'>
    expect(planCall(c).map((p) => `${p.part}:${p.verb}`)).toEqual(['thinking:null', 'text:say', 'tool_use:null', 'text:answer'])
  })
})

describe('lines, clamps and the output split (DET-13, DET-23, DET-24)', () => {
  it('counts lines without counting a trailing newline', () => {
    expect(countLines('a\nb\n')).toBe(2)
    expect(countLines('')).toBe(0)
    expect(splitLines('a\n\nb')).toEqual(['a', '', 'b'])
  })
  it('a text clamps when it is longer than the limit, and the expander counts its lines', () => {
    expect(clampOf('1\n2\n3', 3)).toEqual({ lines: 3, clamped: false })
    expect(clampOf('1\n2\n3\n4', 3)).toEqual({ lines: 4, clamped: true })
  })
  it('a wrapped paragraph counts its measured lines', () => {
    expect(clampOf('one long paragraph', 3, 9)).toEqual({ lines: 9, clamped: true })
    expect(clampOf('a\nb\nc\nd', 3, 2).lines).toBe(4)
  })
  const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join('\n')
  it('25 lines or fewer show whole', () => {
    const v = outputView(lines(25), null)
    expect(v.head).toHaveLength(25)
    expect(v.hidden).toBe(0)
    expect(v.tail).toEqual([])
  })
  it('26 lines or more show the first 20, "N more lines" and the last 5', () => {
    const v = outputView(lines(214), null)
    expect(v.head).toHaveLength(20)
    expect(v.head[19]).toBe('line 20')
    expect(v.hidden).toBe(189)
    expect(v.middle).toHaveLength(189)
    expect(v.tail).toEqual(['line 210', 'line 211', 'line 212', 'line 213', 'line 214'])
    expect(v.partial).toBe(false)
  })
  it('an output too large to fetch whole takes its tail from the separate fetch and drops the cut lines', () => {
    const first = lines(500) + '\nline 50' // the first fetch ends in the middle of a line
    const tail = 'ne 9990\nline 9991\nline 9992\nline 9993\nline 9994\nline 9995\n'
    const v = outputView(first, tail)
    expect(v.partial).toBe(true)
    expect(v.head).toHaveLength(20)
    expect(v.tail).toEqual(['line 9991', 'line 9992', 'line 9993', 'line 9994', 'line 9995'])
  })
  it('formats sizes', () => {
    expect(fmtBytes(640)).toBe('640 B')
    expect(fmtBytes(11 * 1024)).toBe('11 KB')
    expect(fmtBytes(1536)).toBe('1.5 KB')
    expect(fmtBytes(3 * 1024 * 1024)).toBe('3 MB')
  })
  it('pretty-prints JSON input and leaves other text alone', () => {
    expect(prettyJson('{"a":1}')).toBe('{\n  "a": 1\n}')
    expect(prettyJson('not json')).toBe('not json')
  })
})

describe('the agent calls ledger (DET-34)', () => {
  const c = (id: string, usd: number) => ({ ...call(id, { cost_usd: usd }) }) as AgentDetail['top_calls'][number]
  it('shows the five costliest and "N other calls · $X" that add up to the agent cost to the cent', () => {
    // 0.1 + 0.2 + 0.2 + 0.2 + 0.2 shown as cents; the remainder is what is left of the total
    const detail = { cost_usd: 3.28, top_calls: [c('a', 1.114), c('b', 0.785), c('c', 0.552), c('d', 0.333), c('e', 0.2)], other_calls: { n: 7, cost_usd: 0.296 } }
    const l = callsLedger(detail)
    expect(l.top).toHaveLength(5)
    expect(l.count).toBe(12)
    const shown = l.top.reduce((s, x) => s + Math.round(x.cost_usd! * 100), 0)
    const other = Number(/\$(\d+\.\d\d)/.exec(l.otherText!)![1])
    expect(shown + Math.round(other * 100)).toBe(328)
    expect(l.otherText).toMatch(/^7 other calls · \$/)
  })
  it('has no other line when every call is in the top five', () => {
    expect(callsLedger({ cost_usd: 1, top_calls: [c('a', 1)], other_calls: { n: 0, cost_usd: null } }).otherText).toBeNull()
  })
  it('says "1 other call" for one', () => {
    expect(callsLedger({ cost_usd: 1, top_calls: [c('a', 0.5)], other_calls: { n: 1, cost_usd: 0.5 } }).otherText).toBe('1 other call · $0.50')
  })
})

describe('gap lines (DET-41, DET-42)', () => {
  const next = (label: string, display_id: string | null = 'T7'): NextTurn => ({ turn: 'x/turn/7', kind: 'human', origin: 'typed', label, display_id })
  it('infers a usage limit from the first 60 characters of the next turn', () => {
    const text = 'I hit my usage limit and now it is back, please continue from where you left off with the work'
    expect(limitInference({ state: 'limit' }, next(text))).toBe(`Inferred: the next turn starts with “${text.slice(0, 60)}”.`)
  })
  it('only a usage-limit gap carries it', () => {
    expect(limitInference({ state: 'you' }, next('x'))).toBeNull()
    expect(limitInference({ state: 'limit' }, null)).toBeNull()
  })
  it('names the turn a gap leads into, with 120 characters of its text', () => {
    expect(startsTurnLine(next('y'.repeat(200)))).toBe(`Starts turn 7: ${'y'.repeat(120)}`)
    expect(startsTurnLine(null)).toBeNull()
  })
})

describe('text states (DET-63, DET-64, DET-67, DET-50)', () => {
  it('tells encrypted Codex reasoning, a reasoning summary, redacted and empty thinking apart', () => {
    expect(thinkingState('reasoning', '')).toEqual({ kind: 'encrypted' })
    expect(thinkingState('reasoning', 'summary')).toEqual({ kind: 'text', heading: 'Reasoning summary' })
    expect(thinkingState('redacted_thinking', '')).toEqual({ kind: 'redacted' })
    expect(thinkingState('thinking', '   ')).toEqual({ kind: 'empty' })
    expect(thinkingState('thinking', 'hm')).toEqual({ kind: 'text', heading: 'Thinking' })
  })
  it('replaces an inline base64 image with one line carrying its size', () => {
    const payload = 'A'.repeat(4000)
    const pieces = splitImages(`before data:image/png;base64,${payload} after`)
    expect(pieces).toEqual([
      { kind: 'text', text: 'before ' },
      { kind: 'image', bytes: 3000 },
      { kind: 'text', text: ' after' },
    ])
    expect(imageLine(3000)).toBe('Image not shown (2.9 KB)')
  })
  it('names the sender of an agent message from its tag, else from the origin', () => {
    expect(senderOf('teammate', 'x <teammate-message teammate_id="reviewer" color="blue">hi')).toBe('reviewer')
    expect(senderOf('peer', 'hi')).toBe('peer')
    expect(senderOf('typed', 'hi')).toBeNull()
  })
  it('counts characters, not bytes', () => {
    expect(charCount('héllo 😀')).toBe(7)
  })
  it('a step names a gap, the turn message or an op', () => {
    expect(stepKind('gap-1700', 't')).toBe('gap')
    expect(stepKind('t', 't')).toBe('message')
    expect(stepKind('s/llm/1', 't')).toBe('op')
  })
})
