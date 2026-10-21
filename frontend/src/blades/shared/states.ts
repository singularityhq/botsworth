import type { CSSProperties } from 'react'
import type { Stretch, Substate, TurnKind } from '../../api/types'
import { GLYPH, type IconName } from '../../ui/glyphs'

/**
 * Seven wall states (SES-15), their colours (SES-20), the gap kinds of the turn list (SES-36, TRN-34)
 * and the turn kinds of SES-34. Shared by the session blade, the turn blade and the detail blade.
 */

export const STATE_KEYS = ['model', 'tool', 'agents', 'you', 'agentmsg', 'limit', 'none'] as const
export type StateKey = (typeof STATE_KEYS)[number]

export interface StateInfo {
  /** Full label (tooltips, gap rows, legend tooltip). */
  label: string
  /** Legend short form. */
  short: string
  /** Hatched means nothing ran (SES-15, SES-20). */
  hatched: boolean
}

export const STATES: Record<StateKey, StateInfo> = {
  model: { label: 'Model working', short: 'model', hatched: false },
  tool: { label: 'Tools running', short: 'tools', hatched: false },
  agents: { label: 'Only agents running', short: 'agents', hatched: false },
  you: { label: 'Waiting on you', short: 'waiting on you', hatched: true },
  agentmsg: { label: "Waiting for an agent's message", short: 'waiting on agent', hatched: true },
  limit: { label: 'Usage limit (inferred)', short: 'usage limit', hatched: true },
  none: { label: 'Nothing running', short: 'nothing running', hatched: true },
}

/** SES-16: a stretch becomes a gap row only at 2 min or more. */
export const GAP_MIN_MS = 120_000
/** SES-26: the legend lists states with a total of 30 s or more. */
export const LEGEND_MIN_MS = 30_000
export const LEGEND_MAX = 4

/** SES-20: the hatch. `colour` is a CSS colour expression, always a token. */
export const hatch = (colour: string) => `repeating-linear-gradient(135deg, ${colour} 0 2px, transparent 2px 5px)`

/** Fill of a state's swatch, bar segment or gap bar: solid when something ran, hatched with a 1 px inset outline when nothing did (SES-20). */
export function stateFill(k: StateKey): CSSProperties {
  const colour = `var(--st-${k})`
  if (!STATES[k].hatched) return { background: colour }
  return { background: hatch(colour), boxShadow: `inset 0 0 0 1px ${colour}` }
}

// ---- gaps -----------------------------------------------------------------

/** What a gap shows as: its state, except that the `none`/`before_first`, `none`/`stalled` and `tool`/`background` or `tool`/`test` stretches are their own kinds (SES-18, SES-19a, D56). */
export type GapKind = 'you' | 'agentmsg' | 'limit' | 'before_first' | 'stalled' | 'background' | 'none' | 'agents'

type GapLike = Pick<Stretch, 'state' | 'substate'>

export function gapKind(g: GapLike): GapKind {
  if (g.state === 'none' && g.substate === 'before_first') return 'before_first'
  if (g.state === 'none' && g.substate === 'stalled') return 'stalled'
  if (g.state === 'tool') {
    if (g.substate !== 'background' && g.substate !== 'test') throw new Error(`a tool stretch has substate background or test (DM-52), got ${String(g.substate)}`)
    return 'background'
  }
  return g.state
}

/** The state a gap counts under. `before_first` and `stalled` count under `none`, `background` (also `tool`/`test`) under `tool` (SES-18, SES-19a, D41 M11). */
export function gapState(g: GapLike): StateKey {
  return g.state
}

export const GAP_LABEL: Record<GapKind, string> = {
  you: STATES.you.label,
  agentmsg: STATES.agentmsg.label,
  limit: STATES.limit.label,
  before_first: 'Before the first message',
  stalled: 'Stalled',
  background: 'Background command',
  none: STATES.none.label,
  agents: STATES.agents.label,
}

/** SES-36 / D38 L4 glyphs; "Only agents running" uses `bot` (TRN-34). */
export const GAP_GLYPH: Record<GapKind, IconName> = {
  you: GLYPH.gapWaiting,
  agentmsg: GLYPH.gapAgentMessage,
  limit: GLYPH.gapUsageLimit,
  before_first: GLYPH.gapWaiting,
  stalled: GLYPH.gapWaiting,
  background: GLYPH.toolShell,
  none: GLYPH.gapWaiting,
  agents: GLYPH.subAgent,
}

/** SES-15 substate wording, used by the legend tooltip, gap reasons and the detail blade. */
export const SUBSTATE_WORDING = {
  ask: 'asked you a question',
  permission: 'waited for your approval',
  reply: 'the next turn is your message',
  background: 'a background command ran',
  test: 'a test run ran',
  wait: 'a sleep or polling command ran',
  stalled: 'a finished task waited for an agent that was not running',
} as const
export type WordedSubstate = keyof typeof SUBSTATE_WORDING

/** The substates with wording per state, in SES-15 order. The legend tooltip of `you`, `tool` and `agents` lists them (SES-15); `none`/`stalled` has its own legend item (D56). */
export const LEGEND_SUBSTATES: Partial<Record<StateKey, readonly WordedSubstate[]>> = {
  you: ['ask', 'permission', 'reply'],
  tool: ['background', 'test', 'wait'],
  agents: ['test', 'wait'],
}

/** TRN-32: the flag on a tool row that waited on the user. */
export const USER_WAIT_FLAG: Record<'ask' | 'permission', string> = {
  ask: 'asked you',
  permission: SUBSTATE_WORDING.permission,
}

/** DET-21: the info line under a tool's duration. */
export const USER_WAIT_NOTE: Record<'ask' | 'permission', string> = {
  ask: 'This tool waits for your answer. Its duration includes your reply.',
  permission: 'This call waited for your approval. Its duration includes the wait and the run after it.',
}

export interface GapContext {
  /** The session's first numbered turn is a `human` turn (SES-36 "before its first message" otherwise). */
  firstTurnIsHuman: boolean
  /** Names of the engagements running (agents-only stretches, TRN-34). */
  running?: readonly (string | null)[]
}

/** SES-36 reasons, with TRN-34's reason for agents-only stretches and for waits on a call. */
export function gapReason(g: GapLike, ctx: GapContext): string {
  switch (gapKind(g)) {
    case 'you':
      return youReason(g.substate)
    case 'limit':
      return 'the next turn resumes after a usage limit'
    case 'agentmsg':
      return "the next turn is an agent's message"
    case 'before_first':
      return ctx.firstTurnIsHuman ? 'the session was open before your first message' : 'the session was open before its first message'
    case 'stalled':
      return 'a finished task waited for an agent that was not running'
    case 'background':
      return g.substate === 'test' ? 'a background test run ran; nothing else did' : 'a background command ran; nothing else did'
    case 'none':
      return 'no model call, tool or agent was running'
    case 'agents': {
      const names = (ctx.running ?? []).map((n) => n ?? 'agent') // FALLBACK-OK: TRN-34 (an engagement without a name is listed as "agent")
      const shown = names.slice(0, 3).join(', ')
      const more = names.length > 3 ? ` +${names.length - 3}` : ''
      return `${plural(names.length, 'agent')} running${names.length > 0 ? `: ${shown}${more}` : ''}`
    }
  }
}

function youReason(substate: Substate): string {
  switch (substate) {
    case 'ask':
      return `an agent ${SUBSTATE_WORDING.ask}`
    case 'permission':
      return `an agent ${SUBSTATE_WORDING.permission}`
    case 'reply':
      return SUBSTATE_WORDING.reply
    default:
      throw new Error(`a you stretch has substate ask, permission or reply (DM-50), got ${String(substate)}`)
  }
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

// ---- turn kinds (SES-34 item 2) --------------------------------------------

export type KindKey = 'message' | 'slash' | 'resume' | 'agent' | 'plugin' | 'scripted' | 'harness'

export interface KindInfo {
  key: KindKey
  icon: IconName
  label: string
  /** SES-34 item 3: these names are 14/500 ink1; the others 14/400 ink2. */
  lead: boolean
}

const KINDS: Record<KindKey, KindInfo> = {
  message: { key: 'message', icon: GLYPH.turnHuman, label: 'Your message', lead: true },
  slash: { key: 'slash', icon: GLYPH.turnCommand, label: 'Slash command', lead: true },
  resume: { key: 'resume', icon: GLYPH.turnResume, label: 'Resume after usage limit', lead: true },
  agent: { key: 'agent', icon: GLYPH.turnAgentMessage, label: 'Message from an agent', lead: false },
  plugin: { key: 'plugin', icon: GLYPH.turnPlugin, label: 'Plugin or system message', lead: false },
  scripted: { key: 'scripted', icon: GLYPH.scriptedPrompt, label: 'Scripted prompt', lead: false },
  harness: { key: 'harness', icon: GLYPH.fold, label: 'Session event', lead: false },
}

/** SES-17: a human turn whose text, trimmed, starts with one of these (case-insensitive) resumes after a usage limit. */
const RESUME_PREFIXES = ['i hit my usage limit', 'please continue from where you left off', 'continue from where you left off']
export function isResumeText(text: string): boolean {
  const t = text.trim().toLowerCase()
  return RESUME_PREFIXES.some((p) => t.startsWith(p))
}

const AGENT_ORIGINS = new Set(['peer', 'coordinator', 'teammate', 'agent'])
const SCRIPTED_ORIGINS = new Set(['sdk', 'exec'])

/** SES-34 item 2 (kind table; S2 DM-20 kinds and origins). */
export function kindOf(kind: TurnKind, origin: string, label: string): KindInfo {
  if (kind === 'harness') return KINDS.harness
  if (kind === 'human') {
    if (origin === 'slash') return KINDS.slash
    if (isResumeText(label)) return KINDS.resume
    return KINDS.message
  }
  if (AGENT_ORIGINS.has(origin)) return KINDS.agent
  if (SCRIPTED_ORIGINS.has(origin)) return KINDS.scripted
  // `plugin:<name>`, `system`; FALLBACK-OK: DM-20 (an origin outside the enumerated list is shown as a plugin or system message)
  return KINDS.plugin
}
