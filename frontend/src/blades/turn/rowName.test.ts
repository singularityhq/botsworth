import { describe, expect, it } from 'vitest'
import { agent, call, gap, tool } from './testRows'
import {
  agentName, callName, durText, failureText, firstLine, gapName, plainName, silenceText, skillSub, stripCd, toolGlyph, toolName, toolNamesOfCalls,
} from './rowName'

const step = (verb: 'think' | 'say' | 'answer', summary: string, snippet = '') => ({ step_id: 's', verb, summary, snippet })

describe('callName (TRN-31)', () => {
  it('uses the first line of the said text', () => {
    expect(callName(call('a', { steps: [step('say', '\n  Fixing the sync\nmore text')] }), ['Bash'])).toEqual({ text: 'Fixing the sync', final: false, muted: false })
  })
  it('falls back to the snippet when the summary is empty', () => {
    expect(callName(call('a', { steps: [step('say', '', 'From the snippet')] }), []).text).toBe('From the snippet')
  })
  it('tags a final answer', () => {
    expect(callName(call('a', { steps: [step('answer', 'Done. All tests pass.')] }), [])).toEqual({ text: 'Done. All tests pass.', final: true, muted: false })
  })
  it('prefers said text over thinking, in step order', () => {
    expect(callName(call('a', { steps: [step('think', 'Hmm'), step('say', 'Saying')] }), []).text).toBe('Saying')
  })
  it('uses the first line of thinking in ink2 when nothing was said', () => {
    expect(callName(call('a', { steps: [step('think', 'Plan the fix')] }), ['Read'])).toEqual({ text: 'Plan the fix', final: false, muted: true })
  })
  it('names the tools it asked for when it said and thought nothing', () => {
    expect(callName(call('a'), ['Read', 'Grep'])).toEqual({ text: 'Tool request: Read, Grep', final: false, muted: true })
  })
  it('says "Thinking only" when there is no text and no tool', () => {
    expect(callName(call('a', { steps: [step('say', '  ')] }), [])).toEqual({ text: 'Thinking only', final: false, muted: true })
  })
})

describe('toolNamesOfCalls', () => {
  it('collects the tool and agent rows after a call, up to the next call at its depth', () => {
    const rows = [call('1'), tool('t1', { tool_name: 'Read' }), agent('ag', { mode: 'messaged' }), call('2'), tool('t2', { tool_name: 'Bash' })]
    const m = toolNamesOfCalls(rows.map((row) => ({ row, depth: 0 })))
    expect(m.get(rows[0].id)).toEqual(['Read', 'SendMessage'])
    expect(m.get(rows[3].id)).toEqual(['Bash'])
  })
  it('does not give a deeper row to the outer call', () => {
    const rows = [call('1'), tool('t1', { tool_name: 'Read' }), tool('inner', { tool_name: 'Edit' })]
    const m = toolNamesOfCalls([{ row: rows[0], depth: 0 }, { row: rows[1], depth: 0 }, { row: rows[2], depth: 1 }])
    expect(m.get(rows[0].id)).toEqual(['Read'])
  })
})

describe('toolName (TRN-32)', () => {
  it('uses the step summary, then the tool name', () => {
    expect(toolName(tool('a', { summary: 'Run the tests' })).text).toBe('Run the tests')
    expect(toolName(tool('a', { summary: '', tool_name: 'Grep' })).text).toBe('Grep')
  })
  it('shows the target when it differs from the name, and strips a leading cd', () => {
    const t = toolName(tool('a', { summary: 'Run the tests', command: 'cd /Users/a/proj && npm test' }))
    expect(t.target).toBe('npm test')
    expect(toolName(tool('a', { summary: 'npm test', command: 'npm test' })).target).toBeNull()
  })
  it('strips quoted cd paths only at the start', () => {
    expect(stripCd('cd "/a b/c" && ls && cd x && pwd')).toBe('ls && cd x && pwd')
    expect(stripCd("cd '/a' &&   git status")).toBe('git status')
    expect(stripCd('echo cd /x && y')).toBe('echo cd /x && y')
  })
  it('uses the file path for a file tool', () => {
    expect(toolName(tool('a', { tool_name: 'Read', verb: 'read', summary: 'Read the module', command: null, file_path: '/src/rbac.py' })).target).toBe('/src/rbac.py')
  })
  it('picks the glyph by tool, then by verb', () => {
    expect(toolGlyph(tool('a', { tool_name: 'Bash' }))).toBe('terminal')
    expect(toolGlyph(tool('a', { tool_name: 'Edit' }))).toBe('file-pen')
    expect(toolGlyph(tool('a', { tool_name: 'WebFetch' }))).toBe('globe')
    expect(toolGlyph(tool('a', { tool_name: 'mcp__x__y', verb: 'search' }))).toBe('search-code')
    expect(toolGlyph(tool('a', { tool_name: 'mcp__x__y', verb: 'custom' }))).toBe('puzzle')
  })
})

describe('skill loads (TRN-32, SES-55)', () => {
  it('names the skill with book-open and a sub-text per skill_via', () => {
    const viaTool = toolName(tool('a', { skill_load: 'writing-specs', skill_via: 'tool' }))
    expect(viaTool).toMatchObject({ glyph: 'book-open', text: 'writing-specs', skill: true, sub: 'skill loaded' })
    expect(toolName(tool('a', { skill_load: 'x', skill_via: 'read' })).sub).toBe('SKILL.md read')
    expect(toolName(tool('a', { skill_load: 'x', skill_via: 'slash' })).sub).toBe('skill loaded')
    expect(skillSub(null)).toBe('skill loaded')
  })
})

describe('failure flags (TRN-32)', () => {
  it('prints the exit code for a non-zero exit', () => {
    expect(failureText(tool('a', { failed: true, exit_code: 2, status: 'exit:2' }))).toBe('exit 2')
  })
  it('reads the exit code from a step outcome when exit_code is missing', () => {
    expect(failureText(tool('a', { failed: true, exit_code: null, status: 'exit:127' }))).toBe('exit 127')
  })
  it('says interrupted, then error', () => {
    expect(failureText(tool('a', { failed: true, interrupted: true, exit_code: null }))).toBe('interrupted')
    expect(failureText(tool('a', { failed: true, is_error: true, exit_code: null }))).toBe('error')
  })
  it('is null for a tool that exited 0', () => {
    expect(failureText(tool('a'))).toBeNull()
    expect(failureText(tool('a', { status: 'exit:0' }))).toBeNull()
  })
  it('flags silence from 5 min', () => {
    expect(silenceText({ max_silence_ms: 299_999 })).toBeNull()
    expect(silenceText({ max_silence_ms: 300_000 })).toBe('no records 5m')
    expect(silenceText({ max_silence_ms: null })).toBeNull()
  })
})

describe('durations', () => {
  it('prefixes ≈ when the time is derived', () => {
    expect(durText(45_000, 'derived')).toBe('≈45s')
    expect(durText(45_000, 'present')).toBe('45s')
    expect(durText(null, 'derived')).toBe('')
  })
})

describe('agentName (TRN-33)', () => {
  it('joins the names and says started or messaged with the description', () => {
    const a = agent('x', { agent_names: ['explorer', 'tests'], description: 'Survey the repo\nline 2' })
    expect(agentName(a, false)).toMatchObject({ text: 'explorer, tests', sub: 'started · Survey the repo', rows: '5 rows', inRange: null })
    expect(agentName(agent('x', { mode: 'messaged' }), true)).toMatchObject({ sub: 'messaged', rows: 'rows shown' })
  })
  it('falls back to the type, then to "agent"', () => {
    expect(agentName(agent('x', { agent_names: [], agent_type: 'Explore' }), false).text).toBe('Explore')
    expect(agentName(agent('x', { agent_names: [], agent_type: null }), false).text).toBe('agent')
  })
  it('adds "N of M calls" in a zoom', () => {
    expect(agentName(agent('x'), false, { n_calls_in_range: 2, n_calls: 9 }).inRange).toBe('2 of 9 calls')
    expect(agentName(agent('x', { n_rows: 1 }), false).rows).toBe('1 row')
  })
})

describe('gapName (TRN-34)', () => {
  it('gives glyph, duration, label and reason', () => {
    expect(gapName(gap(0, 3720, { state: 'limit', substate: null }), true)).toEqual({ glyph: 'timer-off', dur: '1h02m', label: 'Usage limit (inferred)', reason: 'the next turn resumes after a usage limit' })
  })
  it('names up to three running agents and counts the rest', () => {
    const running = ['a', 'b', 'c', 'd'].map((n) => ({ row: n, agent_name: n, start_ms: 0, end_ms: 1, cost_usd: null }))
    const g = gapName(gap(0, 300, { state: 'agents', substate: null, running }), true)
    expect(g).toMatchObject({ glyph: 'bot', label: 'Only agents running', reason: '4 agents running: a, b, c +1' })
  })
})

describe('plainName', () => {
  it('prints one line per row type', () => {
    expect(plainName(call('a'))).toBe('Thinking only')
    expect(plainName(tool('a', { summary: 'Run the tests' }))).toBe('Run the tests')
    expect(plainName(agent('x'))).toBe('x')
    expect(plainName(gap(0, 150))).toBe('3m Nothing running')
    expect(firstLine(null)).toBe('')
  })
})
