import { describe, expect, it } from 'vitest'
import { GAP_GLYPH, GAP_LABEL, gapReason, gapState, isResumeText, kindOf, STATES, STATE_KEYS, stateFill } from './states'

describe('SES-15 states', () => {
  it('has seven states; the first three are solid and the last four are hatched', () => {
    expect(STATE_KEYS).toHaveLength(7)
    expect(STATE_KEYS.map((k) => STATES[k].hatched)).toEqual([false, false, false, true, true, true, true])
  })

  it('hatched fill is a 135 degree 2/5 px stripe with a 1 px inset outline; solid fill is one colour (SES-20)', () => {
    const solid = stateFill('model')
    expect(solid.background).toBe('var(--st-model)')
    expect(solid.boxShadow).toBeUndefined()
    const hatched = stateFill('limit')
    expect(hatched.background).toBe('repeating-linear-gradient(135deg, var(--st-limit) 0 2px, transparent 2px 5px)')
    expect(hatched.boxShadow).toBe('inset 0 0 0 1px var(--st-limit)')
  })
})

describe('gap kinds (SES-36, D41 M11)', () => {
  it('before_first counts under none; every other label is its own state', () => {
    expect(gapState('before_first')).toBe('none')
    expect(gapState('you')).toBe('you')
    expect(gapState('limit')).toBe('limit')
    expect(gapState('agents')).toBe('agents')
  })

  it('uses hourglass for you, none and before_first, timer-off for the limit and ear for an agent message (D38 L4)', () => {
    expect(GAP_GLYPH.you).toBe('hourglass')
    expect(GAP_GLYPH.none).toBe('hourglass')
    expect(GAP_GLYPH.before_first).toBe('hourglass')
    expect(GAP_GLYPH.limit).toBe('timer-off')
    expect(GAP_GLYPH.agentmsg).toBe('ear')
    expect(GAP_GLYPH.agents).toBe('bot')
  })

  it('marks the usage limit as inferred in its label', () => {
    expect(GAP_LABEL.limit).toBe('Usage limit (inferred)')
  })

  it('gives each label its reason', () => {
    const c = { firstTurnIsHuman: true }
    expect(gapReason('you', c)).toBe('the next turn is your message')
    expect(gapReason('limit', c)).toBe('the next turn resumes after a usage limit')
    expect(gapReason('agentmsg', c)).toBe("the next turn is an agent's message")
    expect(gapReason('none', c)).toBe('no model call, tool or agent was running')
  })

  it('before the first message says "your" only when the first turn is a human turn', () => {
    expect(gapReason('before_first', { firstTurnIsHuman: true })).toBe('the session was open before your first message')
    expect(gapReason('before_first', { firstTurnIsHuman: false })).toBe('the session was open before its first message')
  })

  it('agents-only names up to three engagements and counts the rest', () => {
    const c = { firstTurnIsHuman: true }
    expect(gapReason('agents', { ...c, running: ['a', 'b'] })).toBe('2 agents running: a, b')
    expect(gapReason('agents', { ...c, running: ['a'] })).toBe('1 agent running: a')
    expect(gapReason('agents', { ...c, running: ['a', 'b', 'c', 'd', 'e'] })).toBe('5 agents running: a, b, c +2')
  })
})

describe('turn kinds (SES-34, SES-17)', () => {
  it('maps human origins', () => {
    expect(kindOf('human', 'typed', 'fix it').label).toBe('Your message')
    expect(kindOf('human', 'queued', 'fix it').label).toBe('Your message')
    expect(kindOf('human', 'slash', '/mad:bootstrap').label).toBe('Slash command')
  })

  it('a human turn that starts with a usage-limit phrase resumes after a usage limit, case-insensitive and trimmed', () => {
    expect(isResumeText('  I hit my usage limit. Please continue')).toBe(true)
    expect(isResumeText('please CONTINUE from where you left off')).toBe(true)
    expect(isResumeText('Continue from where you left off, thanks')).toBe(true)
    expect(isResumeText('what about my usage limit?')).toBe(false)
    expect(kindOf('human', 'typed', 'I hit my usage limit').label).toBe('Resume after usage limit')
  })

  it('maps message origins', () => {
    for (const o of ['peer', 'coordinator', 'teammate', 'agent']) expect(kindOf('message', o, 'x').label).toBe('Message from an agent')
    for (const o of ['sdk', 'exec']) expect(kindOf('message', o, 'x').label).toBe('Scripted prompt')
    expect(kindOf('message', 'plugin:e2e-grid', 'x').label).toBe('Plugin or system message')
    expect(kindOf('message', 'system', 'x').label).toBe('Plugin or system message')
  })

  it('"Your message", "Slash command" and "Resume" names are the lead; the other kinds are quiet', () => {
    expect(kindOf('human', 'typed', 'x').lead).toBe(true)
    expect(kindOf('human', 'slash', 'x').lead).toBe(true)
    expect(kindOf('human', 'typed', 'I hit my usage limit').lead).toBe(true)
    expect(kindOf('message', 'peer', 'x').lead).toBe(false)
    expect(kindOf('message', 'sdk', 'x').lead).toBe(false)
  })
})
