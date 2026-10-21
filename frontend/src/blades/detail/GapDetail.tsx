import type { AnyRow, GapRow, NextTurn } from '../../api/types'
import { fmt } from '../../lib/format'
import { gapKind, gapReason, GAP_LABEL, gapState, stateFill } from '../shared/states'
import { clockSec } from '../turn/turn.logic'
import { ContextRow } from './ContextRow'
import { GAP_AGENTS_MAX, gapCaption, limitInference, startsTurnLine } from './detail.logic'
import { Hero, HeroFigure, Section } from './parts'
import { rowTitle, type DetailCtx } from './rowTitle'
import { rowStart } from '../turn/rowList.logic'

export interface GapDetailProps {
  ctx: DetailCtx
  gap: GapRow
  turnWallMs: number | null
  sessionWallMs: number | null
  /** The row before the gap in As-it-ran order; null at the turn's start (DET-42). */
  before: AnyRow | null
  /** The next main-thread row, in this turn or (when the gap ends the turn) the next one. */
  after: AnyRow | null
  /** The turn the `after` row starts, when it is not in this turn. */
  startsTurn: NextTurn | null
  /** Nothing follows: the gap ends the session. */
  sessionEnd: boolean
}

function RowLink({ ctx, r }: { ctx: DetailCtx; r: AnyRow }) {
  const t = rowTitle(r, ctx)
  const start = rowStart(r)
  return <ContextRow lead={start === null ? undefined : clockSec(start, ctx.tz)} glyph={t.glyph} name={t.text} onClick={() => ctx.goTo(r.id)} />
}

/** DET-40 to DET-43: a gap. Facts only (D29): what the data says about the stretch and what is next to it. */
export function GapDetailView({ ctx, gap, turnWallMs, sessionWallMs, before, after, startsTurn, sessionEnd }: GapDetailProps) {
  const caption = gapCaption(gap, turnWallMs, sessionWallMs)
  const inference = limitInference(gap, gap.next_turn)
  const running = gap.running.slice(0, GAP_AGENTS_MAX)
  const starts = startsTurn === null ? null : startsTurnLine(startsTurn)
  const reason = gapReason(gap, { firstTurnIsHuman: ctx.firstTurnIsHuman, running: gap.running.map((r) => r.agent_name) })
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Hero>
          <HeroFigure>{fmt.dur(gap.dur_ms)}</HeroFigure>
          {caption ? <span className="text-ink3">{caption}</span> : null}
        </Hero>
        <p className="flex items-center gap-2 text-ink2">
          <i aria-hidden="true" className="inline-block h-2.5 w-2.5 shrink-0 rounded-chip" style={stateFill(gapState(gap))} />
          <span>
            {GAP_LABEL[gapKind(gap)]}: {reason}.
          </span>
        </p>
        {inference ? <p className="text-ink3">{inference}</p> : null}
      </div>
      {before ? (
        <Section title="Before">
          <div className="-mx-2">
            <RowLink ctx={ctx} r={before} />
          </div>
        </Section>
      ) : null}
      <Section title="After">
        {after ? (
          <div className="-mx-2">
            <RowLink ctx={ctx} r={after} />
          </div>
        ) : null}
        {starts ? <p className="text-ink3">{starts}</p> : null}
        {sessionEnd && !after ? <p className="text-ink3">Session end</p> : null}
      </Section>
      {running.length > 0 ? (
        <Section icon="bot" title="Agents running during it" count={gap.running.length}>
          <div className="-mx-2 flex flex-col">
            {running.map((r) => (
              <ContextRow
                key={r.row}
                lead={`${fmt.clock(r.start_ms, ctx.tz)}–${fmt.clock(r.end_ms, ctx.tz)}`}
                wide
                glyph="bot"
                name={r.agent_name ?? 'agent' /* FALLBACK-OK: TRN-34 (an engagement without a name is listed as "agent") */}
                trail={r.cost_usd === null ? null : <span className="fig text-money">{fmt.money(r.cost_usd)}</span>}
                onClick={() => ctx.goTo(r.row)}
              />
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  )
}
