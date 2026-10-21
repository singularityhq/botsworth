import type { AgentRow, AnyRow, CallRow, GapRow, ToolRow } from '../../api/types'
import { fmt } from '../../lib/format'
import type { IconName } from '../../ui/glyphs'
import { GAP_GLYPH, GAP_LABEL, gapKind, gapReason } from '../shared/states'

/**
 * The names and sub-texts of the turn blade's rows (TRN-31 to TRN-34). Pure: wire rows in, text out. The row components draw them and the
 * detail blade (F4b) uses them for its titles, so a row has one name everywhere.
 */

/** First non-empty line of a text, trimmed. */
export function firstLine(text: string | null | undefined): string {
  // FALLBACK-OK: TRN-31 (a step with no text has no first line; the next name rule applies)
  return (text ?? '').split('\n').map((l) => l.trim()).find((l) => l !== '') ?? ''
}

/** TRN-32 (TURN-R §3): a leading `cd <path> && ` is removed from a shown command. */
export function stripCd(command: string): string {
  return command.replace(/^\s*cd\s+(?:"[^"]*"|'[^']*'|\S+)\s*&&\s*/, '')
}

// ---- model call (TRN-31) -----------------------------------------------------------------------------------------------

export interface CallName {
  text: string
  /** The call's answer is a final answer: the row shows the "final" tag. */
  final: boolean
  /** The name is not the call's own words (a thinking line, a tool request, or "Thinking only"): ink2. */
  muted: boolean
}

/**
 * TRN-31: the first line of its said text or final answer; else the first line of its thinking; else "Tool request: <tools>"; else "Thinking
 * only". `toolNames` are the tools this call asked for (see `toolNamesOfCalls`): the wire call carries only its text steps.
 */
export function callName(call: Pick<CallRow, 'steps'>, toolNames: readonly string[]): CallName {
  const line = (s: CallRow['steps'][number]) => firstLine(s.summary) || firstLine(s.snippet)
  const said = call.steps.find((s) => (s.verb === 'say' || s.verb === 'answer') && line(s) !== '')
  if (said) return { text: line(said), final: said.verb === 'answer', muted: false }
  const thought = call.steps.find((s) => s.verb === 'think' && line(s) !== '')
  if (thought) return { text: line(thought), final: false, muted: true }
  if (toolNames.length > 0) return { text: `Tool request: ${toolNames.join(', ')}`, final: false, muted: true }
  return { text: 'Thinking only', final: false, muted: true }
}

/**
 * The tool names each call asked for, by call id, from the rows that follow it up to the next call at the same depth: its tool rows and its
 * agent rows (named for the tool that made them). The wire `CallRow` has no tool list (types.ts), so the names come from the row order.
 */
export function toolNamesOfCalls(nodes: readonly { row: AnyRow; depth: number }[]): Map<string, string[]> {
  const out = new Map<string, string[]>()
  let current: { depth: number; names: string[] } | null = null
  for (const n of nodes) {
    const r = n.row
    if (r.type === 'call') {
      current = { depth: n.depth, names: [] }
      out.set(r.id, current.names)
    } else if (current !== null && n.depth === current.depth) {
      if (r.type === 'tool') current.names.push(r.tool_name ?? r.verb) // FALLBACK-OK: TRN-31 (a tool row with no tool name is called by its verb)
      else if (r.type === 'agent') current.names.push(r.mode === 'messaged' ? 'SendMessage' : 'Agent')
    }
  }
  return out
}

// ---- tool row (TRN-32) ---------------------------------------------------------------------------------------------------

const TOOL_GLYPH: Record<string, IconName> = {
  bash: 'terminal', exec_command: 'terminal', shell: 'terminal', run: 'terminal', exec: 'terminal',
  read: 'file-text',
  edit: 'file-pen', multiedit: 'file-pen', apply_patch: 'file-pen', notebookedit: 'file-pen',
  write: 'file-plus',
  grep: 'search-code', glob: 'search-code', search: 'search-code',
  webfetch: 'globe', websearch: 'globe', web: 'globe', fetch: 'globe',
  skill: 'book-open',
}

/** SYS-28: the glyph of a tool, by tool name, then by verb; anything else is `puzzle`. */
export function toolGlyph(t: Pick<ToolRow, 'tool_name' | 'verb' | 'skill_load'>): IconName {
  if (t.skill_load !== null) return 'book-open'
  const byName = t.tool_name === null ? undefined : TOOL_GLYPH[t.tool_name.toLowerCase()]
  // FALLBACK-OK: SYS-28 (MCP, other and unknown tools share the `puzzle` glyph)
  return byName ?? TOOL_GLYPH[t.verb.toLowerCase()] ?? 'puzzle'
}

export interface ToolName {
  glyph: IconName
  text: string
  /** The target in mono ink3: the command, file path, pattern or URL, when it differs from the name. */
  target: string | null
  /** A skill load (SES-55): ink1 glyph, name at 600. */
  skill: boolean
  /** "skill loaded" or "SKILL.md read" on a skill load. */
  sub: string | null
}

/** TRN-32 skill-load sub-text: `read` is a SKILL.md read; a Skill tool call and a slash invocation both loaded the skill. */
export function skillSub(via: ToolRow['skill_via']): string {
  return via === 'read' ? 'SKILL.md read' : 'skill loaded'
}

export function toolName(t: ToolRow): ToolName {
  if (t.skill_load !== null) return { glyph: 'book-open', text: t.skill_load, target: null, skill: true, sub: skillSub(t.skill_via) }
  // FALLBACK-OK: TRN-32 (the step summary, else the tool name; a tool row with neither is called by its verb)
  const text = t.summary.trim() || t.tool_name || t.verb
  const raw = firstLine(t.command ?? t.file_path ?? t.target) // FALLBACK-OK: TRN-32 (the target is the command, else the file path, else the pattern or URL)
  const target = stripCd(raw)
  return { glyph: toolGlyph(t), text, target: target !== '' && target !== text ? target : null, skill: false, sub: null }
}

/** TRN-32 (QRY-37): the failure flag text, or null when the tool did not fail. */
export function failureText(t: ToolRow): string | null {
  const outcomeExit = t.status === null ? null : /^exit:(\d+)$/.exec(t.status)
  const code = t.exit_code !== null && t.exit_code !== 0 ? t.exit_code : outcomeExit !== null && Number(outcomeExit[1]) !== 0 ? Number(outcomeExit[1]) : null
  if (code !== null) return `exit ${code}`
  if (t.interrupted) return 'interrupted'
  if (t.failed || t.is_error) return 'error'
  return null
}

/** TRN-32: `no records <duration>` from a silence of 5 min or more; the user-wait flags (`asked you`, `waited for your approval`) are in shared/states.ts (DM-40). */
export const SILENCE_FLAG_MS = 300_000
export function silenceText(t: Pick<ToolRow, 'max_silence_ms'>): string | null {
  return t.max_silence_ms !== null && t.max_silence_ms >= SILENCE_FLAG_MS ? `no records ${fmt.dur(t.max_silence_ms)}` : null
}

/** TRN-32: a derived duration is prefixed by `≈` (tooltip "End taken from the next record"). */
export const DERIVED_TIP = 'End taken from the next record'
export function durText(ms: number | null, prov: ToolRow['time_prov']): string {
  if (ms === null) return ''
  return `${prov === 'derived' ? '≈' : ''}${fmt.dur(ms)}`
}

// ---- agent row (TRN-33) --------------------------------------------------------------------------------------------------

export interface AgentName {
  text: string
  /** "started" or "messaged", then " · " and the description when it has one. */
  sub: string
  /** "N rows" folded, "rows shown" open. */
  rows: string
  /** In a zoom: "N of M calls". */
  inRange: string | null
}

export function agentName(a: AgentRow, open: boolean, range?: { n_calls_in_range: number; n_calls: number }): AgentName {
  const names = a.agent_names.filter((n) => n !== '')
  // FALLBACK-OK: TRN-33 (the agent name, else its type; an agent with neither is called "agent")
  const text = names.length > 0 ? names.join(', ') : (a.agent_type ?? 'agent')
  const desc = firstLine(a.description)
  return {
    text,
    sub: `${a.mode}${desc !== '' ? ` · ${desc}` : ''}`,
    rows: open ? 'rows shown' : `${a.n_rows} ${a.n_rows === 1 ? 'row' : 'rows'}`,
    inRange: range ? `${range.n_calls_in_range} of ${range.n_calls} calls` : null,
  }
}

// ---- gap row (TRN-34) ----------------------------------------------------------------------------------------------------

export interface GapName {
  glyph: IconName
  dur: string
  label: string
  reason: string
}

export function gapName(g: GapRow, firstTurnIsHuman: boolean): GapName {
  return {
    glyph: GAP_GLYPH[gapKind(g)],
    dur: fmt.dur(g.dur_ms),
    label: GAP_LABEL[gapKind(g)],
    reason: gapReason(g, { firstTurnIsHuman, running: g.running.map((r) => r.agent_name) }),
  }
}

// ---- one line of text per row (titles, tooltips, toasts, the search reminder) ---------------------------------------------

export interface PlainCtx {
  /** Tool names per call id (`toolNamesOfCalls`). */
  toolNames?: ReadonlyMap<string, readonly string[]>
  firstTurnIsHuman?: boolean
}

/** The row's name as one string: what the toast, the breadcrumb and the detail title print. */
export function plainName(r: AnyRow, ctx: PlainCtx = {}): string {
  switch (r.type) {
    case 'call':
      // FALLBACK-OK: TRN-31 (a call whose tool names are not known yet is named from its text steps alone)
      return callName(r, ctx.toolNames?.get(r.id) ?? []).text
    case 'tool':
      return toolName(r).text
    case 'agent':
      return agentName(r, false).text
    case 'gap': {
      const g = gapName(r, ctx.firstTurnIsHuman ?? true) // FALLBACK-OK: TRN-34 (the reason wording for "before the first message" only matters for a gap, and defaults to a human first turn)
      return `${g.dur} ${g.label}`
    }
  }
}
