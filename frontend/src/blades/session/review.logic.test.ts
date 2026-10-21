import { describe, expect, it } from 'vitest'
import { fixtures } from '../../api/__fixtures__'
import type { Cause, Finding, ReviewReport, TimeSplit } from '../../api/types'
import { CAUSE_KEYS, CAUSES, causeFill } from '../shared/causes'
import { bars, CHANGE, claim, evidenceText, facts, footnote, heading, legend, largestCause, threadsText, youFacts } from './review.logic'

const R = fixtures.sessionReview as ReviewReport
const finding = (cause: Finding['cause']) => R.findings.find((f) => f.cause === cause)!
const withCauses = (over: Partial<Record<Cause, number>>, lost = 0): Pick<ReviewReport, 'wall_ms' | 'lost_ms' | 'causes'> => {
  const causes = CAUSE_KEYS.map((cause) => ({ cause, ms: over[cause] ?? 0 }))
  return { wall_ms: causes.reduce((a, c) => a + c.ms, 0), lost_ms: lost, causes }
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

  it('says nothing was lost below 60 s, and names the largest other cause', () => {
    const r = withCauses({ working: 600_000, you: 300_000, stalled: 30_000 }, 30_000)
    expect(claim(r)).toEqual(['16m wall. Nothing was lost to a stall.', 'The largest cause was waiting on you (5m).'])
  })

  it('omits the second sentence when every cause other than working is under 2 min', () => {
    expect(claim(withCauses({ working: 600_000, tests: 119_000, idle: 100_000 }))).toEqual(['14m wall. Nothing was lost to a stall.'])
  })

  it('lets a cause of exactly 2 min name itself', () => {
    expect(largestCause(withCauses({ working: 600_000, polling: 120_000 }).causes)).toEqual({ cause: 'polling', ms: 120_000 })
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

  it('lists the legend largest first, causes present only', () => {
    expect(legend(R.causes).map((c) => c.cause)).toEqual(['stalled', 'working', 'tests', 'you', 'idle', 'polling', 'message'])
  })
})

describe('REV-6 card text', () => {
  const split: TimeSplit = {
    parts: [
      { state: 'you', substate: 'reply', ms: 3_960_000 },
      { state: 'you', substate: 'ask', ms: 720_000 },
      { state: 'you', substate: 'permission', ms: 0 },
      { state: 'model', substate: null, ms: 5000 },
    ],
  }

  it('words each heading as a claim', () => {
    expect(heading(finding('stalled'))).toBe('Stalled 1h35m: finished tasks waited for main, reviewer, builder +1 while nothing ran')
    expect(heading(finding('tests'))).toBe('Tests held the session for 20m')
    expect(heading(finding('polling'))).toBe('Polling loops held the session for 5m')
    expect(heading(finding('you'))).toBe('Waited 15m on you')
    expect(heading({ ...finding('you'), cause: 'message', ms: 7_500_000 })).toBe("Waited 2h05m for an agent's message")
  })

  it('says "an agent" when a stalled finding has no threads, and lists at most three', () => {
    expect(heading({ ...finding('stalled'), threads: [] })).toBe('Stalled 1h35m: finished tasks waited for an agent while nothing ran')
    expect(threadsText(['a', 'b', 'c'])).toBe('a, b, c')
    expect(threadsText(['a', 'b', 'c', 'd', 'e'])).toBe('a, b, c +2')
  })

  it('states the facts of each cause', () => {
    expect(facts(finding('stalled'), R, split)).toBe('2 stalls of 2 min or more.')
    expect(facts(finding('tests'), R, split)).toBe('9 test runs: 3 with failures, 5 passed, 1 with no result. 2 ran in the background.')
    expect(facts(finding('polling'), R, split)).toBe('4 sleep or polling commands.')
    expect(facts({ ...finding('you'), cause: 'message', n: 1 }, R, split)).toBe(
      '1 wait of 2 min or more. Botsworth does not store message text, so it cannot say why.',
    )
  })

  it('omits the zero clauses of the tests facts', () => {
    const r = { test_runs: { n: 4, run_ms: 1, n_failed: 0, n_passed: 4, n_no_result: 0, n_background: 0 } }
    expect(facts(finding('tests'), r, split)).toBe('4 test runs: 4 passed.')
    expect(facts(finding('tests'), { test_runs: { ...r.test_runs, n: 1, n_passed: 0, n_no_result: 0 } }, split)).toBe('1 test run.')
  })

  it('splits the you card by substate in SES-15 order and leaves out substates with no time', () => {
    expect(youFacts(split)).toBe('Asked you a question 12m · the next turn is your message 1h06m')
    expect(facts(finding('you'), R, split)).toBe('Asked you a question 12m · the next turn is your message 1h06m')
  })

  it('shows the evidence line as range, duration and turn, without the turn when it is null', () => {
    expect(evidenceText(finding('stalled'), 'UTC')).toBe('Longest: 06:28–07:30, 1h02m · turn 2')
    const f = finding('stalled')
    expect(evidenceText({ ...f, longest: { ...f.longest!, turn: null, display_id: null } }, 'UTC')).toBe('Longest: 06:28–07:30, 1h02m')
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
