import type { CSSProperties } from 'react'
import type { Cause } from '../../api/types'
import { hatch } from './states'

/** REV-1: the eight causes of wall time, in DM-56 order. One table for the header claim, the bars and the cards. */
export const CAUSE_KEYS: readonly Cause[] = ['stalled', 'tests', 'polling', 'you', 'message', 'limit', 'working', 'idle']

export interface CauseInfo {
  label: string
  /** Lower case, for the legend and for sentences. */
  short: string
  /** The colour token, as a CSS variable name. */
  token: string
  hatched: boolean
  /** Legend tooltip: what hatching means for this cause (SES-20; `polling` ran a command that only waited). */
  note: string
}

const NOTHING_RAN = 'nothing ran'

export const CAUSES: Record<Cause, CauseInfo> = {
  stalled: { label: 'Stalled', short: 'stalled', token: '--color-error', hatched: true, note: NOTHING_RAN },
  tests: { label: 'Tests', short: 'tests', token: '--st-tool', hatched: false, note: 'a test run ran' },
  polling: { label: 'Polling', short: 'polling', token: '--st-tool', hatched: true, note: 'a sleep or polling command ran' },
  you: { label: 'Waiting on you', short: 'waiting on you', token: '--st-you', hatched: true, note: NOTHING_RAN },
  message: { label: "Waiting for an agent's message", short: 'waiting on agent', token: '--st-agentmsg', hatched: true, note: NOTHING_RAN },
  limit: { label: 'Usage limit (inferred)', short: 'usage limit', token: '--st-limit', hatched: true, note: NOTHING_RAN },
  working: { label: 'Working', short: 'working', token: '--st-model', hatched: false, note: 'a call, tool or agent ran' },
  idle: { label: 'Nothing running', short: 'nothing running', token: '--st-none', hatched: true, note: NOTHING_RAN },
}

/** SES-20 fill: solid when something ran, hatched with a 1 px inset outline otherwise. */
export function causeFill(c: Cause): CSSProperties {
  const { token, hatched } = CAUSES[c]
  const colour = `var(${token})`
  return hatched ? { background: hatch(colour), boxShadow: `inset 0 0 0 1px ${colour}` } : { background: colour }
}

/** REV-2, REV-4, REV-5: lost time is shown from 60 s. */
export const LOST_MIN_MS = 60_000
