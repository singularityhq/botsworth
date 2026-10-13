import type { AnyRow } from '../../api/types'
import type { IconName } from '../../ui/glyphs'
import { agentName, callName, gapName, toolName } from '../turn/rowName'
import { fmt } from '../../lib/format'

/** What the detail needs from the turn blade, so each kind of detail stays a plain component. */
export interface DetailCtx {
  session: string
  tz: string
  /** DET-5: move the turn's cursor to a row; the detail follows. */
  goTo: (id: string) => void
  /** A row of the turn (or of a loaded agent band) by id, when the turn's data holds it. */
  lookup: (id: string) => AnyRow | undefined
  /** TRN-31: the tools each call asked for, by call id. */
  toolNames: ReadonlyMap<string, readonly string[]>
  firstTurnIsHuman: boolean
}

export interface RowTitle {
  glyph: IconName
  text: string
}

/**
 * DET-3: the title of a row is its name in the turn blade (TRN-31 to TRN-34), so a row has one name everywhere. A call whose tools are not
 * known (it is outside the loaded rows) and which has no text of its own is "Model call", not "Thinking only".
 */
export function rowTitle(r: AnyRow, ctx: Pick<DetailCtx, 'toolNames' | 'firstTurnIsHuman'>): RowTitle {
  switch (r.type) {
    case 'call': {
      const names = ctx.toolNames.get(r.id) ?? [] // FALLBACK-OK: DET-3 (tool names exist only for calls in the loaded rows)
      if (names.length === 0 && r.steps.length === 0) return { glyph: 'arrow-left-right', text: 'Model call' }
      return { glyph: 'arrow-left-right', text: callName(r, names).text }
    }
    case 'tool': {
      const n = toolName(r)
      return { glyph: n.glyph, text: n.text }
    }
    case 'agent':
      return { glyph: 'bot', text: agentName(r, false).text }
    case 'gap': {
      const g = gapName(r, ctx.firstTurnIsHuman)
      return { glyph: g.glyph, text: `${g.dur} ${g.label}` }
    }
  }
}

/** `HH:MM–HH:MM` for a gap (DET-2). */
export const clockRange = (start: number, end: number, tz: string): string => `${fmt.clock(start, tz)}–${fmt.clock(end, tz)}`
