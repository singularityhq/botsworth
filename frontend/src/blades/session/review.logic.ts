import type { Cause, CausePart, Finding, ReviewReport } from '../../api/types'
import { fmt } from '../../lib/format'
import { CAUSES, LOST_MIN_MS } from '../shared/causes'
import { GAP_MIN_MS, SUBSTATE_WORDING } from '../shared/states'

/** Review (REV-2 to REV-7): the text and the numbers. Every figure is a field of the API-33 response or of the session's `time_split` (REV-9). */

/** REV-5: a bar segment of this share of the wall or more carries its label. */
export const LABEL_SHARE = 0.06

/** "3 stalls", "1 stall": a count in the SYS number format (thousands separators) with its noun. */
function plural(n: number, word: string): string {
  return `${fmt.count(n)} ${word}${n === 1 ? '' : 's'}`
}

export const hasLoss = (r: Pick<ReviewReport, 'lost_ms'>): boolean => r.lost_ms >= LOST_MIN_MS

type ClaimInput = Pick<ReviewReport, 'wall_ms' | 'lost_ms' | 'causes' | 'n_sessions' | 'n_unmeasured'>

/** REV-4 item 1: the claim, one or two sentences. A scope's `wall_ms` is a sum over sessions, so REV-22 says "of session time in <n> sessions".
 *  Where stalls are not measured (Codex and omp), the claim says so instead of "nothing was lost". */
export function claim(r: ClaimInput, scope = false): string[] {
  const wall = fmt.dur(r.wall_ms)
  const lead = scope ? `${wall} of session time in ${plural(r.n_sessions, 'session')}.` : `${wall} wall.`
  const unmeasured = r.n_sessions > 0 && r.n_unmeasured === r.n_sessions
  let stalls: string
  if (unmeasured) stalls = scope ? 'Stalls are not measured for these sessions.' : 'Stalls are not measured for this session.'
  else {
    const part = r.n_unmeasured > 0 ? ` (${fmt.count(r.n_unmeasured)} not measured)` : ''
    // 60 s and more is shown as a figure; under that, "Under a minute" never contradicts a stalled legend item; 0 is nothing (REV-4).
    if (hasLoss(r)) stalls = `${fmt.dur(r.lost_ms)} was lost to stalls${part}.`
    else stalls = r.lost_ms > 0 ? `Under a minute was lost to stalls${part}.` : `Nothing was lost to a stall${part}.`
  }
  const largest = largestCause(r.causes)
  return largest === null ? [`${lead} ${stalls}`] : [`${lead} ${stalls}`, `The largest cause was ${CAUSES[largest.cause].label.toLowerCase()} (${fmt.dur(largest.ms)}).`]
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

/** REV-5 legend: the causes present, "stalled" first, then the rest largest first (ties keep DM-56 order). */
export function legend(causes: readonly CausePart[]): CausePart[] {
  const present = causes.filter((c) => c.ms > 0)
  return [...present.filter((c) => c.cause === 'stalled'), ...present.filter((c) => c.cause !== 'stalled').sort((a, b) => b.ms - a.ms)]
}

// ---- cards (REV-6) -------------------------------------------------------------

/** The stalled heading's list: "a", "a and b", "a, b and c", and over 3 "a, b, c and N more"; `main` is "the main thread". */
export function threadsText(threads: readonly string[]): string {
  const names = threads.map((t) => (t === 'main' ? 'the main thread' : t))
  if (names.length > 3) return `${names.slice(0, 3).join(', ')} and ${fmt.count(names.length - 3)} more`
  if (names.length < 2) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** REV-6 heading: a claim. */
export function heading(f: Finding, scope = false): string {
  const what = scope ? 'these sessions' : 'the session'
  const ms = fmt.dur(f.ms)
  switch (f.cause) {
    case 'stalled':
      return `Stalled ${ms}: finished tasks waited for ${f.threads.length === 0 ? 'an agent' : threadsText(f.threads)} while nothing ran`
    case 'tests':
      return `Tests held ${what} for ${ms}`
    case 'polling':
      return `Polling loops held ${what} for ${ms}`
    case 'you':
      return `Waited ${ms} on you`
    case 'message':
      return `Waited ${ms} for an agent's message`
  }
}

/** The `you` substates of `Review.you_parts` over 0 s, in SES-15 order and wording, the first letter upper case. */
export function youFacts(parts: ReviewReport['you_parts']): string {
  return (['ask', 'permission', 'reply'] as const)
    .filter((sub) => parts[sub] > 0)
    .map((sub) => `${SUBSTATE_WORDING[sub]} ${fmt.dur(parts[sub])}`)
    .join(' · ')
    .replace(/^./, (c) => c.toUpperCase())
}

/** REV-6 facts line. */
export function facts(f: Finding, r: Pick<ReviewReport, 'test_runs' | 'you_parts'>): string {
  switch (f.cause) {
    case 'stalled':
      return `${plural(f.n, 'stall')} of 2 min or more.`
    case 'tests': {
      const t = r.test_runs
      const clauses = [
        t.n_failed > 0 ? `${fmt.count(t.n_failed)} with failures` : null,
        t.n_passed > 0 ? `${fmt.count(t.n_passed)} passed` : null,
        t.n_no_result > 0 ? `${fmt.count(t.n_no_result)} with no result` : null,
      ].filter((c) => c !== null)
      const head = `${plural(t.n, 'test run')}${clauses.length > 0 ? `: ${clauses.join(', ')}` : ''}.`
      return t.n_background > 0 ? `${head} ${fmt.count(t.n_background)} ran in the background.` : head
    }
    case 'polling':
      return `${plural(f.n, 'sleep or polling command')}.`
    case 'you':
      return youFacts(r.you_parts)
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
export function turnNumber(displayId: string): string {
  const m = /^T(\d+)$/.exec(displayId)
  if (m === null) throw new Error(`a turn display id is T<number> (QRY-42), got ${displayId}`)
  return m[1]
}

/** True when the evidence row is in no turn's list (agent work attributed to no turn, DM-45): its line is plain text, not a link (REV-6). */
export const outsideATurn = (f: Finding): boolean => f.longest !== null && f.longest.turn === null

/** REV-6 evidence line text, without the link wrapper; null when there is no longest. REV-22: a scope's line names the session and the day, and has no turn number. */
export function evidenceText(f: Finding, tz: string, scope = false): string | null {
  const e = f.longest
  if (e === null) return null
  const when = `${fmt.range(e.start_ms, e.end_ms, tz)}, ${fmt.dur(e.dur_ms)}`
  const outside = e.turn === null ? ' · agent work outside a turn' : ''
  if (scope) return `Longest: ${e.name}, ${fmt.date(e.start_ms, tz)} ${when}${outside}`
  return `Longest: ${when}${outside}${e.turn !== null && e.display_id !== null ? ` · turn ${turnNumber(e.display_id)}` : ''}`
}

/** REV-4 footnote with the REV-7 addition for sessions whose logs record no task notices; REV-22 words the additions for a scope. */
export function footnote(r: Pick<ReviewReport, 'n_unmeasured'>, scope = false): string[] {
  const base = 'Measured from the log. Only stalls count as lost: a finished task waited for an agent that was not running. Tests and polling count only while nothing else on that thread ran.'
  const out = [base]
  if (scope) out.push('Sessions count whole (a session active in the period counts in full), so a longest example can lie outside the period.')
  if (r.n_unmeasured > 0) {
    out.push(
      scope
        ? r.n_unmeasured === 1
          ? '1 session is Codex or omp: stalls are not measured for it, because its log does not record when a background task finishes.'
          : `${fmt.count(r.n_unmeasured)} sessions are Codex or omp: stalls are not measured for them, because their logs do not record when a background task finishes.`
        : 'Stalls are not measured for Codex and omp sessions: their logs do not record when a background task finishes.',
    )
  }
  return out
}

/** REV-22: "Ddd HH:MM", the start that tells two same-named session links apart. */
export function startText(ms: number, tz: string): string {
  return `${fmt.date(ms, tz).split(' ')[0]} ${fmt.clock(ms, tz)}`
}

/** REV-22: the links of a card that share their name with another link of the same card. */
export function sharedNames(sessions: readonly { name: string }[]): Set<string> {
  const seen = new Set<string>()
  const dup = new Set<string>()
  for (const x of sessions) (seen.has(x.name) ? dup : seen).add(x.name)
  return dup
}
