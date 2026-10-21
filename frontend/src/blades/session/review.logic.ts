import type { Cause, CausePart, Finding, ReviewReport, TimeSplit } from '../../api/types'
import { fmt } from '../../lib/format'
import { CAUSES, LOST_MIN_MS } from '../shared/causes'
import { GAP_MIN_MS, SUBSTATE_WORDING, plural } from '../shared/states'

/** Review (REV-2 to REV-7): the text and the numbers. Every figure is a field of the API-33 response or of the session's `time_split` (REV-9). */

/** REV-5: a bar segment of this share of the wall or more carries its label. */
export const LABEL_SHARE = 0.06

export const hasLoss = (r: Pick<ReviewReport, 'lost_ms'>): boolean => r.lost_ms >= LOST_MIN_MS

/** REV-4 item 1: the claim, one or two sentences. */
export function claim(r: Pick<ReviewReport, 'wall_ms' | 'lost_ms' | 'causes'>): string[] {
  const wall = fmt.dur(r.wall_ms)
  const first = hasLoss(r) ? `${wall} wall. ${fmt.dur(r.lost_ms)} was lost to stalls.` : `${wall} wall. Nothing was lost to a stall.`
  const largest = largestCause(r.causes)
  return largest === null ? [first] : [first, `The largest cause was ${CAUSES[largest.cause].label.toLowerCase()} (${fmt.dur(largest.ms)}).`]
}

/** The cause with the most time other than `working`; null when every one of them is under 2 min. The first in DM-56 order wins a tie. */
export function largestCause(causes: readonly CausePart[]): CausePart | null {
  let best: CausePart | null = null
  for (const c of causes) if (c.cause !== 'working' && (best === null || c.ms > best.ms)) best = c
  return best !== null && best.ms >= GAP_MIN_MS ? best : null
}

export interface BarSegment {
  cause: Cause
  ms: number
  /** Share of the wall, 0 to 1. */
  share: number
  labelled: boolean
  tooltip: string
}

export interface Bar {
  caption: string
  totalMs: number
  /** Share of the full width: the wall is 1. */
  width: number
  segments: BarSegment[]
}

/** REV-5 share in a tooltip: whole percent of the wall, "<1" below that. */
function shareText(share: number): string {
  const n = Math.round(share * 100)
  return `${n < 1 ? '<1' : n} %`
}

function bar(caption: string, parts: readonly CausePart[], wallMs: number): Bar {
  const segments = parts
    .filter((p) => p.ms > 0)
    .map((p) => {
      const share = p.ms / wallMs
      return { cause: p.cause, ms: p.ms, share, labelled: share >= LABEL_SHARE, tooltip: `${CAUSES[p.cause].label} ${fmt.dur(p.ms)} (${shareText(share)})` }
    })
  const totalMs = segments.reduce((a, s) => a + s.ms, 0)
  return { caption, totalMs, width: totalMs / wallMs, segments }
}

/** REV-5: "As it ran" and, when the loss is shown, "Without the stalls", which is shorter by exactly `lost_ms`. */
export function bars(r: Pick<ReviewReport, 'wall_ms' | 'lost_ms' | 'causes'>): Bar[] {
  const ran = bar('As it ran', r.causes, r.wall_ms)
  if (!hasLoss(r)) return [ran]
  return [ran, bar('Without the stalls', r.causes.filter((c) => c.cause !== 'stalled'), r.wall_ms)]
}

/** REV-5 legend: the causes present, largest first (ties keep DM-56 order). */
export function legend(causes: readonly CausePart[]): CausePart[] {
  return causes.filter((c) => c.ms > 0).sort((a, b) => b.ms - a.ms)
}

// ---- cards (REV-6) -------------------------------------------------------------

/** "reviewer, builder, scout +1": at most 3 names, then "+N". */
export function threadsText(threads: readonly string[]): string {
  const shown = threads.slice(0, 3).join(', ')
  return threads.length > 3 ? `${shown} +${threads.length - 3}` : shown
}

/** REV-6 heading: a claim. */
export function heading(f: Finding): string {
  const ms = fmt.dur(f.ms)
  switch (f.cause) {
    case 'stalled':
      return `Stalled ${ms}: finished tasks waited for ${f.threads.length === 0 ? 'an agent' : threadsText(f.threads)} while nothing ran`
    case 'tests':
      return `Tests held the session for ${ms}`
    case 'polling':
      return `Polling loops held the session for ${ms}`
    case 'you':
      return `Waited ${ms} on you`
    case 'message':
      return `Waited ${ms} for an agent's message`
  }
}

/** The `you` substates of the session's time split over 0 s, in SES-15 order and wording, the first letter upper case. */
export function youFacts(split: TimeSplit): string {
  return (['ask', 'permission', 'reply'] as const)
    .map((sub) => ({ sub, ms: split.parts.filter((p) => p.state === 'you' && p.substate === sub).reduce((a, p) => a + p.ms, 0) }))
    .filter((x) => x.ms > 0)
    .map((x) => `${SUBSTATE_WORDING[x.sub]} ${fmt.dur(x.ms)}`)
    .join(' · ')
    .replace(/^./, (c) => c.toUpperCase())
}

/** REV-6 facts line. */
export function facts(f: Finding, r: Pick<ReviewReport, 'test_runs'>, split: TimeSplit): string {
  switch (f.cause) {
    case 'stalled':
      return `${plural(f.n, 'stall')} of 2 min or more.`
    case 'tests': {
      const t = r.test_runs
      const clauses = [
        t.n_failed > 0 ? `${t.n_failed} with failures` : null,
        t.n_passed > 0 ? `${t.n_passed} passed` : null,
        t.n_no_result > 0 ? `${t.n_no_result} with no result` : null,
      ].filter((c) => c !== null)
      const head = `${plural(t.n, 'test run')}${clauses.length > 0 ? `: ${clauses.join(', ')}` : ''}.`
      return t.n_background > 0 ? `${head} ${t.n_background} ran in the background.` : head
    }
    case 'polling':
      return `${plural(f.n, 'sleep or polling command')}.`
    case 'you':
      return youFacts(split)
    case 'message':
      return `${plural(f.n, 'wait')} of 2 min or more. Botsworth does not store message text, so it cannot say why.`
  }
}

/** REV-6 change line; `you` has none. */
export const CHANGE: Record<Finding['cause'], string | null> = {
  stalled: 'Wake an agent when its background task finishes, or check sessions where nothing has run for 15 min.',
  tests: 'Run the tests a change touches; run the whole suite once at the end.',
  polling: 'Use background notifications instead of sleep and polling loops.',
  message: 'Not a tooling fix: look at what the other session was doing.',
  you: null,
}

/** The turn number K of a display id "T<K>" (REV-6, as in SES-51 toasts). */
function turnNumber(displayId: string): string {
  const m = /^T(\d+)$/.exec(displayId)
  if (m === null) throw new Error(`a turn display id is T<number> (QRY-42), got ${displayId}`)
  return m[1]
}

/** REV-6 evidence line text, without the link wrapper; null when there is no longest. */
export function evidenceText(f: Finding, tz: string): string | null {
  const e = f.longest
  if (e === null) return null
  return `Longest: ${fmt.range(e.start_ms, e.end_ms, tz)}, ${fmt.dur(e.dur_ms)}${e.display_id === null ? '' : ` · turn ${turnNumber(e.display_id)}`}`
}

/** REV-4 footnote with the REV-7 addition for sessions whose logs record no task notices. */
export function footnote(r: Pick<ReviewReport, 'n_unmeasured'>): string[] {
  const base = 'Measured from the log. Only stalls count as lost: a finished task waited for an agent that was not running. Tests and polling count only while nothing else on that thread ran.'
  return r.n_unmeasured > 0 ? [base, 'Stalls are not measured for Codex and omp sessions: their logs do not record when a background task finishes.'] : [base]
}
