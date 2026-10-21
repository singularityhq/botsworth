import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { Cause, Finding, ReviewReport } from '../../api/types'
import { CAUSE_KEYS, CAUSES, causeFill } from '../shared/causes'
import { bars, CHANGE, claim, evidenceText, facts, footnote, heading, legend, largestCause, outsideATurn, sharedNames, startText, threadsText, youFacts } from './review.logic'
import { fmt } from '../../lib/format'

const R = fixtures.sessionReview as ReviewReport
const finding = (cause: Finding['cause']) => R.findings.find((f) => f.cause === cause)!
const withCauses = (over: Partial<Record<Cause, number>>, lost = 0): Pick<ReviewReport, 'wall_ms' | 'lost_ms' | 'causes' | 'n_sessions' | 'n_unmeasured'> => {
  const causes = CAUSE_KEYS.map((cause) => ({ cause, ms: over[cause] ?? 0 }))
  return { wall_ms: causes.reduce((a, c) => a + c.ms, 0), lost_ms: lost, causes, n_sessions: 1, n_unmeasured: 0 }
}

describe('REV-1 cause table', () => {
  it('has the eight causes in DM-56 order with the labels, short forms and fills of the spec', () => {
    expect(CAUSE_KEYS).toEqual(['stalled', 'tests', 'polling', 'you', 'message', 'limit', 'working', 'idle'])
    expect(CAUSE_KEYS.map((c) => [CAUSES[c].label, CAUSES[c].short, CAUSES[c].token, CAUSES[c].hatched])).toEqual([
      ['Stalled', 'stalled', '--color-error', true],
      ['Tests', 'tests', '--st-tool', false],
      ['Polling', 'polling', '--st-tool', true],
      ['Waiting on you', 'waiting on you', '--st-you', true],
      ["Waiting for an agent's message", 'waiting on agent', '--st-agentmsg', true],
      ['Usage limit (inferred)', 'usage limit', '--st-limit', true],
      ['Working', 'working', '--st-model', false],
      ['Nothing running', 'nothing running', '--st-none', true],
    ])
  })

  it('draws a hatched cause with the SES-20 hatch and a solid cause in one colour, tokens only', () => {
    expect(causeFill('tests')).toEqual({ background: 'var(--st-tool)' })
    expect(causeFill('polling').background).toBe('repeating-linear-gradient(135deg, var(--st-tool) 0 2px, transparent 2px 5px)')
    expect(causeFill('stalled').boxShadow).toBe('inset 0 0 0 1px var(--color-error)')
  })

  it('uses the SES-15 name of the agent-message state, so a cause has one name on every screen', async () => {
    const { STATES } = await import('../shared/states')
    expect(CAUSES.message.label).toBe(STATES.agentmsg.label)
    expect(CAUSES.message.short).toBe(STATES.agentmsg.short)
    expect(CAUSES.you.label).toBe(STATES.you.label)
    expect(CAUSES.limit.short).toBe(STATES.limit.short)
  })

  it('says in the polling legend note that a command ran', () => {
    expect(CAUSES.polling.note).toBe('a sleep or polling command ran')
  })
})

describe('REV-4 claim', () => {
  it('names wall, lost time and the largest cause other than working', () => {
    expect(claim(R)).toEqual(['3h37m wall. 1h35m was lost to stalls.', 'The largest cause was stalled (1h35m).'])
  })

  it('says nothing was lost at 0, "under a minute" from 1 ms to 59.999 s, and the figure from 60 s; and names the largest other cause', () => {
    const at = (lost: number) => claim(withCauses({ working: 600_000, you: 300_000, stalled: lost }, lost))
    expect(at(0)).toEqual(['15m wall. Nothing was lost to a stall.', 'The largest cause was waiting on you (5m).'])
    expect(at(13_000)).toEqual(['15m wall. Under a minute was lost to stalls.', 'The largest cause was waiting on you (5m).'])
    expect(at(59_999)[0]).toBe('16m wall. Under a minute was lost to stalls.')
    expect(at(60_000)[0]).toBe('16m wall. 1m was lost to stalls.')
  })

  it('omits the second sentence when every cause other than working is under 2 min', () => {
    expect(claim(withCauses({ working: 600_000, tests: 119_000, idle: 100_000 }))).toEqual(['14m wall. Nothing was lost to a stall.'])
  })

  it('lets a cause of exactly 2 min name itself', () => {
    expect(largestCause(withCauses({ working: 600_000, polling: 120_000 }).causes)).toEqual({ cause: 'polling', ms: 120_000 })
  })
})

describe('REV-4 and REV-22 claim where stalls are not measured', () => {
  it('says stalls are not measured for this session when the session is unmeasured (a Codex or omp session)', () => {
    expect(claim({ ...R, n_unmeasured: 1, lost_ms: 0 })[0]).toBe('3h37m wall. Stalls are not measured for this session.')
    expect(claim({ ...R, n_unmeasured: 1 })[0]).toBe('3h37m wall. Stalls are not measured for this session.')
  })

  it('says stalls are not measured for these sessions when every session in a scope is unmeasured', () => {
    expect(claim({ ...R, n_sessions: 4, n_unmeasured: 4 }, true)[0]).toBe('3h37m of session time in 4 sessions. Stalls are not measured for these sessions.')
  })

  it('ends the stall clause with the count when only some sessions are unmeasured', () => {
    expect(claim({ ...R, n_sessions: 4, n_unmeasured: 1 }, true)[0]).toBe('3h37m of session time in 4 sessions. 1h35m was lost to stalls (1 not measured).')
    expect(claim({ ...R, n_sessions: 4, n_unmeasured: 2, lost_ms: 0 }, true)[0]).toBe('3h37m of session time in 4 sessions. Nothing was lost to a stall (2 not measured).')
  })
})

describe('REV-5 bars', () => {
  it('draws the second bar shorter by exactly lost_ms, and both sum to their totals', () => {
    const [ran, without] = bars(R)
    expect(ran.caption).toBe('As it ran')
    expect(without.caption).toBe('Without the stalls')
    expect(ran.segments.reduce((a, s) => a + s.ms, 0)).toBe(R.wall_ms)
    expect(without.segments.reduce((a, s) => a + s.ms, 0)).toBe(R.wall_ms - R.lost_ms)
    expect(ran.totalMs - without.totalMs).toBe(R.lost_ms)
    expect(ran.width).toBe(1)
    expect(without.width).toBeCloseTo((R.wall_ms - R.lost_ms) / R.wall_ms, 12)
    expect(without.segments.some((s) => s.cause === 'stalled')).toBe(false)
  })

  it('omits zero parts, keeps the REV-1 order, labels segments of 6 % or more and words the tooltip', () => {
    const [ran] = bars(R)
    expect(ran.segments.map((s) => s.cause)).toEqual(['stalled', 'tests', 'polling', 'you', 'message', 'working', 'idle'])
    expect(ran.segments.filter((s) => s.labelled).map((s) => s.cause)).toEqual(['stalled', 'tests', 'you', 'working'])
    expect(ran.segments[0].tooltip).toBe('Stalled 1h35m (44 %)')
    expect(ran.segments[4].tooltip).toBe("Waiting for an agent's message 1m (<1 %)")
  })

  it('draws one bar when less than 60 s was lost', () => {
    expect(bars(withCauses({ working: 600_000, stalled: 59_000 }, 59_000))).toHaveLength(1)
    expect(bars(withCauses({ working: 600_000, stalled: 60_000 }, 60_000))).toHaveLength(2)
  })

  it('lists stalled first when present, even when other causes are larger, then the rest largest first', () => {
    const causes = withCauses({ stalled: 100_000, working: 900_000, tests: 500_000, you: 700_000 }).causes
    expect(legend(causes).map((c) => c.cause)).toEqual(['stalled', 'working', 'you', 'tests'])
    expect(legend(withCauses({ working: 900_000, tests: 500_000 }).causes).map((c) => c.cause)).toEqual(['working', 'tests'])
  })

  it('lists the legend largest first, causes present only', () => {
    expect(legend(R.causes).map((c) => c.cause)).toEqual(['stalled', 'working', 'tests', 'you', 'idle', 'polling', 'message'])
  })
})

describe('REV-6 card text', () => {
  it('words each heading as a claim', () => {
    expect(heading(finding('stalled'))).toBe('Stalled 1h35m: finished tasks waited for the main thread, reviewer, builder and 1 more while nothing ran')
    expect(heading(finding('tests'))).toBe('Tests held the session for 20m')
    expect(heading(finding('polling'))).toBe('Polling loops held the session for 5m')
    expect(heading(finding('you'))).toBe('Waited 15m on you')
    expect(heading({ ...finding('you'), cause: 'message', ms: 7_500_000 })).toBe("Waited 2h05m for an agent's message")
  })

  it('says "an agent" when a stalled finding has no threads, and lists at most three', () => {
    expect(heading({ ...finding('stalled'), threads: [] })).toBe('Stalled 1h35m: finished tasks waited for an agent while nothing ran')
    expect(threadsText(['a'])).toBe('a')
    expect(threadsText(['a', 'b'])).toBe('a and b')
    expect(threadsText(['a', 'b', 'c'])).toBe('a, b and c')
    expect(threadsText(['reviewer', 'main'])).toBe('reviewer and the main thread')
    expect(threadsText(['a', 'b', 'c', 'main'])).toBe('a, b, c and 1 more')
    expect(threadsText(['main', 'b', 'c', 'd'])).toBe('the main thread, b, c and 1 more')
    expect(threadsText(['a', 'b', 'c', 'd', 'e'])).toBe('a, b, c and 2 more')
  })

  it('states the facts of each cause', () => {
    expect(facts(finding('stalled'), R)).toBe('2 stalls of 2 min or more.')
    expect(facts(finding('tests'), R)).toBe('9 test runs: 3 with failures, 5 passed, 1 with no result. 2 ran in the background.')
    expect(facts(finding('polling'), R)).toBe('4 sleep or polling commands.')
    expect(facts({ ...finding('you'), cause: 'message', n: 1 }, R)).toBe(
      '1 wait of 2 min or more. Botsworth does not store message text, so it cannot say why.',
    )
  })

  it('omits the zero clauses of the tests facts', () => {
    const r = { test_runs: { n: 4, run_ms: 1, n_failed: 0, n_passed: 4, n_no_result: 0, n_background: 0 } }
    expect(facts(finding('tests'), { ...R, ...r })).toBe('4 test runs: 4 passed.')
    expect(facts(finding('tests'), { ...R, test_runs: { ...r.test_runs, n: 1, n_passed: 0, n_no_result: 0 } })).toBe('1 test run.')
  })

  it('splits the you card by substate in SES-15 order and leaves out substates with no time', () => {
    expect(youFacts(R.you_parts)).toBe('Asked you a question 10m · waited for your approval 2m · the next turn is your message 3m')
    expect(facts(finding('you'), R)).toBe('Asked you a question 10m · waited for your approval 2m · the next turn is your message 3m')
    expect(youFacts({ ask: 0, permission: 0, reply: 3_960_000 })).toBe('The next turn is your message 1h06m')
    expect(youFacts({ ask: 0, permission: 0, reply: 0 })).toBe('')
  })

  it('shows the evidence line as range, duration and turn; with turn null it says "agent work outside a turn" instead, also in a scope', () => {
    expect(evidenceText(finding('stalled'), 'UTC')).toBe('Longest: 06:28–07:30, 1h02m · turn 2')
    const f = finding('stalled')
    const outside = { ...f, longest: { ...f.longest!, turn: null, display_id: null } }
    expect(evidenceText(outside, 'UTC')).toBe('Longest: 06:28–07:30, 1h02m · agent work outside a turn')
    expect(evidenceText(outside, 'UTC', true)).toBe('Longest: Fix the importer, Mon Oct 12 06:28–07:30, 1h02m · agent work outside a turn')
    expect(outsideATurn(outside)).toBe(true)
    expect(outsideATurn(f)).toBe(false)
    expect(outsideATurn({ ...f, longest: null })).toBe(false)
    expect(evidenceText(finding('you'), 'UTC')).toBeNull()
  })

  it('gives each cause its fixed change line and the you card none', () => {
    expect(CHANGE.stalled).toBe('Wake an agent when its background task finishes, or check sessions where nothing has run for 15 min.')
    expect(CHANGE.tests).toBe('Run the tests a change touches; run the whole suite once at the end.')
    expect(CHANGE.polling).toBe('Use background notifications instead of sleep and polling loops.')
    expect(CHANGE.message).toBe('Not a tooling fix: look at what the other session was doing.')
    expect(CHANGE.you).toBeNull()
  })
})

describe('REV-7 footnote', () => {
  it('adds the unmeasured line only when sessions are unmeasured', () => {
    expect(footnote({ n_unmeasured: 0 })).toHaveLength(1)
    expect(footnote({ n_unmeasured: 1 })[1]).toBe('Stalls are not measured for Codex and omp sessions: their logs do not record when a background task finishes.')
  })
})

describe('REV-22 scope variants', () => {
  const S = fixtures.scopeReview as ReviewReport

  it('words the claim as session time in n sessions, with and without lost time', () => {
    expect(claim(S, true)).toEqual(['27h47m of session time in 35 sessions. 2h00m was lost to stalls (3 not measured).', 'The largest cause was tests (8h20m).'])
    expect(claim({ ...S, n_unmeasured: 0, lost_ms: 0, n_sessions: 1 }, true)[0]).toBe('27h47m of session time in 1 session. Nothing was lost to a stall.')
    expect(claim({ ...S, n_unmeasured: 0, lost_ms: 13_000 }, true)[0]).toBe('27h47m of session time in 35 sessions. Under a minute was lost to stalls.')
    expect(claim({ ...S, n_unmeasured: 2, lost_ms: 13_000 }, true)[0]).toBe('27h47m of session time in 35 sessions. Under a minute was lost to stalls (2 not measured).')
  })

  it('names the session and the day in the evidence line, with no turn', () => {
    expect(evidenceText(S.findings[0], 'UTC', true)).toBe('Longest: master-control-acme, Mon Oct 12 06:28–07:30, 1h02m')
    expect(evidenceText(S.findings[1], 'UTC', true)).toBeNull()
  })

  it('adds the whole-sessions sentence and the Codex or omp line with the unmeasured count, singular for one', () => {
    expect(footnote({ n_unmeasured: 3 }, true)).toEqual([
      expect.stringMatching(/^Measured from the log\./),
      'Sessions count whole (a session active in the period counts in full), so a longest example can lie outside the period.',
      '3 sessions are Codex or omp: stalls are not measured for them, because their logs do not record when a background task finishes.',
    ])
    expect(footnote({ n_unmeasured: 1 }, true)[2]).toBe('1 session is Codex or omp: stalls are not measured for it, because its log does not record when a background task finishes.')
    expect(footnote({ n_unmeasured: 0 }, true)).toHaveLength(2)
    expect(footnote({ n_unmeasured: 1234 }, true)[2]).toMatch(/^1,234 sessions are /)
  })

  it('says "these sessions" in the tests and polling headings of a scope, and keeps the session wording otherwise', () => {
    const tests = { ...S.findings[0], cause: 'tests' as const, ms: 30_000_000 }
    expect(heading(tests, true)).toBe('Tests held these sessions for 8h20m')
    expect(heading({ ...tests, cause: 'polling' }, true)).toBe('Polling loops held these sessions for 8h20m')
    expect(heading(tests)).toBe('Tests held the session for 8h20m')
    expect(heading({ ...S.findings[0], threads: [] }, true)).toBe('Stalled 2h00m: finished tasks waited for an agent while nothing ran')
  })

  it('tells two same-named links apart by their start, as "Ddd HH:MM"', () => {
    const names = S.findings[0].sessions.map((x) => ({ name: x.name }))
    expect(sharedNames(names).size).toBe(0)
    expect(sharedNames([{ name: 'a' }, { name: 'b' }, { name: 'a' }])).toEqual(new Set(['a']))
    expect(startText(1791786480000, 'UTC')).toBe('Mon 06:28')
  })

  it('uses thousands separators for counts over 999', () => {
    expect(fmt.count(999)).toBe('999')
    expect(fmt.count(1244)).toBe('1,244')
    expect(facts({ ...S.findings[0], n: 1244 }, S)).toBe('1,244 stalls of 2 min or more.')
    expect(facts(S.findings[1], { ...S, test_runs: { ...S.test_runs, n: 2500, n_passed: 1800 } })).toMatch(/^2,500 test runs: 30 with failures, 1,800 passed/)
    expect(claim({ ...S, n_sessions: 1234, n_unmeasured: 0 }, true)[0]).toMatch(/in 1,234 sessions\./)
  })

  it('reads the you facts from the scope review\'s you_parts', () => {
    expect(facts(S.findings[2], S)).toBe('Asked you a question 17m · waited for your approval 8m · the next turn is your message 25m')
  })
})
