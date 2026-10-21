import type { AgentDetail, CallDetail, CallRow, GapRow, NextTurn, ToolRow } from '../../api/types'
import { fmt } from '../../lib/format'
import { gapKind } from '../shared/states'
import { displayIx } from '../session/turnList.logic'
import { failureText, toolGlyph } from '../turn/rowName'

/**
 * The detail blade's rules with a number or an order in them (DET-10 to DET-70). Pure: wire values in, text and counts out. The components
 * only draw what these return.
 */

// ---- tool status (DET-20, D38 L8) ------------------------------------------------------------------------------------------

export interface ToolStatus {
  text: string
  /** The TRN-32 failure rule: drawn with `circle-alert` in `--color-error`. Otherwise ink3 with no glyph. */
  failed: boolean
}

/**
 * DET-20: `exit_code` first, then `interrupted`, then the step's outcome, else nothing. Nothing is never "ok": a tool with no known status shows
 * no status. A failure reads as TRN-32 reads it (`failureText`); exit 0 is a plain ink3 "exit 0".
 */
export function toolStatus(t: Pick<ToolRow, 'exit_code' | 'interrupted' | 'status' | 'failed' | 'is_error'>): ToolStatus | null {
  const failure = failureText({ ...t } as ToolRow)
  if (failure !== null) return { text: failure, failed: true }
  if (t.exit_code === 0) return { text: 'exit 0', failed: false }
  if (t.exit_code === null && t.status !== null && /^exit:0$/.test(t.status)) return { text: 'exit 0', failed: false }
  return null
}

/** DET-21 (DM-40): a silence of 5 minutes or more inside the call. */
export const SILENCE_NOTE_MS = 300_000

/** DET-23: shell tools show a Command; every other tool shows its Input. */
export const isShell = (t: Pick<ToolRow, 'tool_name' | 'verb' | 'skill_load'>): boolean => toolGlyph(t) === 'terminal'

/** DET-23: the call's input JSON, pretty-printed when it parses. */
export function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text // FALLBACK-OK: DET-23 (input that is not JSON is shown as it is)
  }
}

// ---- sizes -----------------------------------------------------------------------------------------------------------------

/** `640 B`, `11 KB`, `1.4 MB`: binary units, 1 decimal below 10. */
export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const unit = (v: number, u: string) => `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10} ${u}`
  if (n < 1024 * 1024) return unit(n / 1024, 'KB')
  if (n < 1024 * 1024 * 1024) return unit(n / (1024 * 1024), 'MB')
  return unit(n / (1024 * 1024 * 1024), 'GB')
}

export const utf8Bytes = (s: string): number => new TextEncoder().encode(s).length

// ---- percent captions (DET-10, DET-30, DET-40) ------------------------------------------------------------------------------

/** SYS-29: integer `N%`, `<1%` below 1 %. Null when there is no whole to take a share of (a turn cost of 0 or null). */
export function share(part: number | null, whole: number | null): string | null {
  if (part === null || whole === null || whole <= 0) return null
  return fmt.pct(part / whole)
}

/** DET-10: "N% of the turn's cost"; DET-30 in a zoom: "N% of the range's cost". Omitted at a turn cost of 0. */
export function costCaption(part: number | null, whole: number | null, basis: 'turn' | 'range' = 'turn'): string | null {
  const s = share(part, whole)
  return s === null ? null : `${s} of the ${basis === 'turn' ? "turn's" : "range's"} cost`
}

/**
 * DET-40: a gap inside the turn's span reads "N% of the turn's wall time"; one between turns or before the first message reads
 * "N% of the session's wall time" (SES-21, SES-36). A gap before the first message is never inside a turn.
 */
export function gapBasis(g: Pick<GapRow, 'state' | 'substate' | 'turn'>): 'turn' | 'session' {
  return gapKind(g) === 'before_first' || g.turn === null ? 'session' : 'turn'
}

export function gapCaption(g: Pick<GapRow, 'state' | 'substate' | 'turn' | 'dur_ms'>, turnWallMs: number | null, sessionWallMs: number | null): string | null {
  const basis = gapBasis(g)
  const s = share(g.dur_ms, basis === 'turn' ? turnWallMs : sessionWallMs)
  return s === null ? null : `${s} of the ${basis === 'turn' ? "turn's" : "session's"} wall time`
}

// ---- model call (DET-11, DET-12) --------------------------------------------------------------------------------------------

export interface TokenPart {
  label: string
  value: number
}

/** DET-12: "in · cache read · cache write · out" and "reasoning" when the call has it (Codex). A null figure is left out of the line. */
export function tokenLine(tok: CallRow['tok']): TokenPart[] {
  const all: [string, number | null][] = [
    ['in', tok.in], ['cache read', tok.cache_read], ['cache write', tok.cache_write], ['out', tok.out], ['reasoning', tok.reasoning],
  ]
  return all.flatMap(([label, value]) => (value === null ? [] : [{ label, value }]))
}

/** DET-11 (DM-47): output tokens per second of model time; "N tok/s out" only when both are above 0. The server's figure stands when it has one. */
export function speedTokS(c: Pick<CallDetail, 'speed_tok_s' | 'tok' | 'dur_ms'>): number | null {
  if (c.speed_tok_s !== null && c.speed_tok_s > 0) return Math.round(c.speed_tok_s)
  const out = c.tok.out
  if (out !== null && out > 0 && c.dur_ms !== null && c.dur_ms > 0) return Math.round(out / (c.dur_ms / 1000))
  return null
}

/** DET-11: "first token 1.2s" (seconds with one decimal below 10 s). */
export function ttftText(ms: number): string {
  return ms < 10_000 ? `first token ${(ms / 1000).toFixed(1)}s` : `first token ${fmt.dur(ms)}`
}

/** DET-14 (DM-37): the time basis is derived from record times. */
export const SPEED_NOTE = 'Model time is taken from log record times, so speed is approximate.'
export const speedNoteShown = (c: Pick<CallRow, 'time_prov'>): boolean => c.time_prov === 'derived'

export type CallSection = CallDetail['sections'][number]
export type ThinkKind = 'thinking'
export interface CallPlan {
  part: CallSection['part']
  section: CallSection
  /** 'say' or 'answer' for a text block (DET-13 "Said" or "Final answer"). */
  verb: 'say' | 'answer' | null
}

/**
 * DET-13: the call's sections in its content order. The i-th text block takes the verb of the i-th `say`/`answer` step; with no step for it a
 * text block of a final call reads as the final answer.
 */
export function planCall(c: Pick<CallDetail, 'sections' | 'steps' | 'is_final'>): CallPlan[] {
  const verbs = c.steps.filter((s) => s.verb === 'say' || s.verb === 'answer').map((s) => s.verb as 'say' | 'answer')
  let text = 0
  return c.sections.map((section) => {
    if (section.part !== 'text') return { part: section.part, section, verb: null }
    const verb = verbs[text] ?? (c.is_final ? 'answer' : 'say') // FALLBACK-OK: DET-13 (a text block with no matching step reads as final only on a final call)
    text += 1
    return { part: 'text', section, verb }
  })
}

// ---- lines, clamps, output (DET-13, DET-23, DET-24, DET-65) -------------------------------------------------------------

/** Lines of a text: split on newlines; one trailing newline does not make another line. An empty text has no lines. */
export function splitLines(text: string): string[] {
  if (text === '') return []
  const lines = text.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

export const countLines = (text: string): number => splitLines(text).length

export interface Clamp {
  /** The number in "Show all · N lines". */
  lines: number
  /** The text is longer than the clamp: the well shows "Show all" . */
  clamped: boolean
}

/**
 * The clamp of a well (3 lines for thinking and asks, 6 for said text and commands). `visual` is the count of wrapped lines the browser
 * measured; the text's own line count is the floor, so a one-paragraph text that wraps over ten lines reads "10 lines".
 */
export function clampOf(text: string, limit: number, visual: number | null = null): Clamp {
  const lines = Math.max(countLines(text), visual ?? 0 /* FALLBACK-OK: DET-13 (no measurement yet: the text's own lines count) */)
  return { lines, clamped: lines > limit }
}

export const OUTPUT_HEAD = 20
export const OUTPUT_TAIL = 5

export interface OutputView {
  head: string[]
  /** Lines between head and tail; their text is in `middle`. */
  hidden: number
  middle: string[]
  tail: string[]
  /** Not all of the output was fetched: the middle is not available and the divider is not a button. */
  partial: boolean
}

/**
 * DET-24: the first 20 lines, a divider "… N more lines" and the last 5; 25 lines or fewer show whole. `tailText` is the separately fetched end
 * of an output too large to fetch whole (DET-65): its first line may be cut by the byte offset, so it is dropped when `tailCut`.
 */
export function outputView(first: string, tailText: string | null, tailCut = true): OutputView {
  if (tailText === null) {
    const lines = splitLines(first)
    if (lines.length <= OUTPUT_HEAD + OUTPUT_TAIL) return { head: lines, hidden: 0, middle: [], tail: [], partial: false }
    return {
      head: lines.slice(0, OUTPUT_HEAD),
      hidden: lines.length - OUTPUT_HEAD - OUTPUT_TAIL,
      middle: lines.slice(OUTPUT_HEAD, lines.length - OUTPUT_TAIL),
      tail: lines.slice(lines.length - OUTPUT_TAIL),
      partial: false,
    }
  }
  // The first fetch ends mid-line: its last line is not trusted. The tail's first line starts mid-line: it is not trusted either.
  const head = splitLines(first).slice(0, -1).slice(0, OUTPUT_HEAD)
  const tailLines = splitLines(tailText)
  const tail = (tailCut ? tailLines.slice(1) : tailLines).slice(-OUTPUT_TAIL)
  return { head, hidden: 0, middle: [], tail, partial: true }
}

// ---- agent (DET-31, DET-34) -------------------------------------------------------------------------------------------------

export const TOP_CALLS = 5

const cents = (usd: number): number => Math.round(usd * 100)

export interface CallsLedger {
  top: CallRow[]
  /** "N other calls · $X"; null when there are no other calls. */
  otherText: string | null
  /** The number beside the "Calls by cost" head. */
  count: number
}

/**
 * DET-34: the agent's five costliest calls, then "N other calls · $X". The five and X add up to the agent's cost to the cent: X is taken as the
 * agent's cost in cents less the five in cents, so rounding each part cannot leave the shown figures a cent apart from the shown total.
 */
export function callsLedger(agent: Pick<AgentDetail, 'cost_usd' | 'top_calls' | 'other_calls'>): CallsLedger {
  const top = agent.top_calls.slice(0, TOP_CALLS)
  const n = agent.other_calls.n
  const count = top.length + n
  if (n === 0) return { top, otherText: null, count }
  const priced = top.every((c) => c.cost_usd !== null)
  const x =
    agent.cost_usd !== null && priced
      ? (cents(agent.cost_usd) - top.reduce((s, c) => s + cents(c.cost_usd as number), 0)) / 100
      : agent.other_calls.cost_usd // FALLBACK-OK: DET-34 (no balancing when the agent or a shown call has no cost; the server's figure stands)
  const money = x === null ? '' : ` · ${fmt.money(x)}`
  return { top, otherText: `${n} other call${n === 1 ? '' : 's'}${money}`, count }
}

/** DM-36, D41 M12: runs are counted per causing call; "N runs" is shown when the row ran more than once. */
export const runsText = (n: number): string | null => (n > 1 ? `${n} runs` : null)

// ---- gap (DET-41, DET-42, DET-43) -------------------------------------------------------------------------------------------

export const clip = (text: string, n: number): string => (text.length > n ? text.slice(0, n) : text)

/** DET-41 (SES-17): the second line under a usage-limit gap. Null for every other gap. */
export function limitInference(g: Pick<GapRow, 'state'>, next: NextTurn | null): string | null {
  if (g.state !== 'limit' || next === null) return null
  return `Inferred: the next turn starts with “${clip(next.label, 60)}”.`
}

/** DET-42: "Starts turn N: <turn text, 120 characters>" under After, when the next row starts another turn. */
export function startsTurnLine(next: NextTurn | null): string | null {
  if (next === null) return null
  const n = displayIx(next.display_id)
  return n === null ? `Starts a turn: ${clip(next.label, 120)}` : `Starts turn ${n}: ${clip(next.label, 120)}` // FALLBACK-OK: DET-42 (a harness turn has no number)
}

/** DET-43: at most 8 engagements that overlap the gap. */
export const GAP_AGENTS_MAX = 8

// ---- ids -------------------------------------------------------------------------------------------------------------------

/** SES-9: what the detail's `step` names. `gap-<start_ms>` is a gap, the turn's own op id is the message, anything else is an op. */
export type StepKind = 'gap' | 'message' | 'op'
export function stepKind(step: string, turnOp: string | null): StepKind {
  if (/^gap-\d+$/.test(step)) return 'gap'
  if (turnOp !== null && step === turnOp) return 'message'
  return 'op'
}

// ---- text states (DET-63, DET-64, DET-67) -----------------------------------------------------------------------------------

/** What a `thinking` source holds, from the content block's type and text (API-26 `block`, DET-63, DET-64). */
export type ThinkingState =
  | { kind: 'text'; heading: 'Thinking' | 'Reasoning summary' }
  | { kind: 'encrypted' }
  | { kind: 'redacted' }
  | { kind: 'empty' }

export function thinkingState(block: string | null, text: string): ThinkingState {
  const hasText = text.trim() !== ''
  if (block === 'reasoning') return hasText ? { kind: 'text', heading: 'Reasoning summary' } : { kind: 'encrypted' }
  if (block === 'redacted_thinking') return { kind: 'redacted' }
  return hasText ? { kind: 'text', heading: 'Thinking' } : { kind: 'empty' }
}

export const IMAGE_BLOCKS: ReadonlySet<string> = new Set(['image', 'input_image'])

export type TextPiece = { kind: 'text'; text: string } | { kind: 'image'; bytes: number }

/** DET-67: an inline base64 image in text is never drawn; it becomes one "Image not shown (size)" piece. */
export function splitImages(text: string): TextPiece[] {
  const re = /data:image\/[a-z0-9.+-]+;base64,([A-Za-z0-9+/=]+)/gi
  const out: TextPiece[] = []
  let at = 0
  for (const m of text.matchAll(re)) {
    if (m.index > at) out.push({ kind: 'text', text: text.slice(at, m.index) })
    out.push({ kind: 'image', bytes: Math.floor((m[1].length * 3) / 4) })
    at = m.index + m[0].length
  }
  if (at < text.length) out.push({ kind: 'text', text: text.slice(at) })
  return out
}

export const imageLine = (bytes: number): string => `Image not shown (${fmtBytes(bytes)})`

// ---- message (DET-50) -----------------------------------------------------------------------------------------------------

const AGENT_ORIGINS: ReadonlySet<string> = new Set(['peer', 'coordinator', 'teammate', 'agent'])

/**
 * DET-50: "from <sender>" for a message from an agent. The wire has no sender field; a teammate message names its sender in the
 * `<teammate-message teammate_id="…">` tag of its text, and any other agent origin is named by the origin itself. Null for every other turn.
 */
export function senderOf(origin: string, text: string | null): string | null {
  if (!AGENT_ORIGINS.has(origin)) return null
  const m = text === null ? null : /<teammate-message[^>]*\bteammate_id="([^"]+)"/.exec(text)
  return m ? m[1] : origin
}

/** DET-50: the head's count, in characters (code points) once the whole text is in. */
export const charCount = (text: string): number => [...text].length
