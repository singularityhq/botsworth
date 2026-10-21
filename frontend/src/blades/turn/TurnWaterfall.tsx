import { useCallback, useMemo } from 'react'
import type { GapRow, TurnRow } from '../../api/types'
import { fmt } from '../../lib/format'
import { modelFamily } from '../../lib/model'
import { EmptyState } from '../../ui/EmptyState'
import { Icon } from '../../ui/Icon'
import { Skeleton } from '../../ui/Skeleton'
import { gapState, stateFill } from '../shared/states'
import { useElementWidth } from '../shared/useElementWidth'
import { AxisHead, Bar, Track } from '../shared/Waterfall'
import { barBox, MIN_BAR_PX } from '../shared/waterfall.logic'
import { BandError, BandSkeleton, ShowMore } from './AgentBand'
import { FinalTag } from './CallRow'
import { RowList, type ScrollRequest } from './RowList'
import { Money } from '../../ui/figures'
import type { RowNode } from './rowList.logic'
import { rowEnd, rowStart, type Item } from './rowList.logic'
import { agentName, callName, durText, failureText, gapName, toolName } from './rowName'
import { clockSec, cursorIndexOf, itemHeight, itemLevel, ROW_H_WF, turnWaterfallAxis, waterfallGrid } from './turn.logic'
import type { MsRange } from '../../state/url'

const BAR_H = 12
const GRID_PAD = '0 20px'

const ROW_CLASS =
  'group/row grid cursor-pointer items-center border-b border-rule-soft text-body transition-colors duration-150 ease-[var(--ease-standard)] hover:bg-l3 focus-visible:outline-offset-[-2px] data-[open=true]:bg-struct-soft data-[open=true]:hover:bg-struct-soft data-[cursor=true]:outline-2 data-[cursor=true]:-outline-offset-2 data-[cursor=true]:outline-struct'

export interface TurnWaterfallProps {
  items: readonly Item[]
  turn: TurnRow
  /** Every gap of 60 s or more in the turn, the short ones too: they fold (TRN-40). */
  gaps: readonly GapRow[]
  zoom: MsRange | null
  real: boolean
  width: number
  tz: string
  cursorId: string | null
  toolNames: ReadonlyMap<string, readonly string[]>
  openAgents: ReadonlySet<string>
  firstTurnIsHuman: boolean
  scroll: ScrollRequest
  rowCount: number
  onOpen: (id: string) => void
  onToggleAgent: (id: string) => void
  onUnfold: (foldKey: string) => void
  onShowAll: () => void
  onBandMore: (agent: string) => void
  onBandRetry: (agent: string) => void
}

/**
 * TRN-40 to TRN-42: the same rows as the Ledger, in the same order, with the same folds, cursor and detail; the figure columns become a time
 * track on the turn axis, tail zone included. Bars: a call in its model family's chart colour, a tool in ink2 (a failed one keeps ink2 and
 * gets the error glyph before its name, SYS-8), an agent in `--st-agents`, a gap hatched in its state colour.
 */
export function TurnWaterfall(p: TurnWaterfallProps) {
  const [headRef, trackW] = useElementWidth<HTMLDivElement>()
  const grid = waterfallGrid(p.width)
  const built = useMemo(() => turnWaterfallAxis(p.turn, p.gaps, p.zoom, p.real, trackW), [p.turn, p.gaps, p.zoom, p.real, trackW])
  const axis = built?.axis ?? null // FALLBACK-OK: TRN-60 (no axis until the track is measured and the turn has a span)
  const tailX = axis && built?.tailFrom != null ? axis.x(built.tailFrom) : null
  const real = p.real || p.zoom !== null
  const cursorIndex = useMemo(() => cursorIndexOf(p.items, p.cursorId), [p.items, p.cursorId])
  const heightOf = useCallback((it: Item) => itemHeight(it, ROW_H_WF), [])

  const render = useCallback(
    (it: Item): React.ReactNode => {
      if (it.type === 'more') return it.kind === 'order' ? <ShowMore n={it.n} cost={<Money value={it.cost} weight={400} />} onMore={p.onShowAll} /> : <ShowMore n={it.n} onMore={() => p.onBandMore(it.agent)} />
      if (it.type === 'band') return it.status === 'loading' ? <BandSkeleton /> : <BandError message={it.message} onRetry={() => p.onBandRetry(it.agent)} />
      const cells = (id: string, key: string, when: number | null, glyph: React.ReactNode, name: React.ReactNode, bars: React.ReactNode, time: React.ReactNode, o: { cursor?: boolean; onClick: () => void; className?: string; title?: string; fold?: boolean }) => (
        <div
          data-row-id={o.fold ? undefined : id}
          data-fold-key={o.fold ? key : undefined}
          data-open={!!o.cursor}
          data-cursor={!!o.cursor}
          role="button"
          tabIndex={-1}
          aria-current={o.cursor || undefined}
          title={o.title}
          onClick={o.onClick}
          className={`${ROW_CLASS} ${o.className ?? /* FALLBACK-OK: TRN-34 (only gap rows add a class) */ ''}`}
          style={{ gridTemplateColumns: grid, columnGap: 12, padding: GRID_PAD, height: ROW_H_WF }}
        >
          <span className="fig text-ink3">{when === null ? '' : clockSec(when, p.tz)}</span>
          <span className="flex">{glyph}</span>
          <span className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">{name}</span>
          <Track>
            {tailX !== null ? <i aria-hidden="true" className="absolute inset-y-0 left-0 bg-l2 group-hover/row:hidden group-data-[open=true]/row:hidden" style={{ width: tailX }} /> : null}
            {axis ? bars : null}
          </Track>
          <span className="flex justify-end">{time}</span>
        </div>
      )
      if (it.type === 'fold') {
        const first = it.firstMs
        const last = it.lastMs
        return cells(it.key, it.key, first, <Icon name="list-collapse" tone="struct" />, (
          <span className="flex min-w-0 items-baseline gap-2 text-struct">
            <span>Show {it.nodes.length} quiet rows</span>
          </span>
        ), first !== null && last !== null && axis ? <Bar box={barBox(axis, first, Math.max(first, last))} style={{ background: 'var(--color-ink3)' }} height={4} /> : null, (
          <span className="fig text-ink2">{first !== null && last !== null ? fmt.dur(last - first) : ''}</span>
        ), { onClick: () => p.onUnfold(it.key), fold: true })
      }
      const n: RowNode = it.node
      const cursor = p.cursorId === n.id
      const open = () => p.onOpen(n.id)
      const r = n.row
      const start = rowStart(r)
      const end = rowEnd(r)
      const bar = (style: React.CSSProperties, title?: string) =>
        axis && start !== null && end !== null ? <Bar box={barBox(axis, start, end, MIN_BAR_PX)} style={style} height={BAR_H} title={title} /> : null
      switch (r.type) {
        case 'call': {
          const name = callName(r, p.toolNames.get(n.id) ?? []) // FALLBACK-OK: TRN-31 (a call whose tool rows are not listed is named from its text)
          return cells(n.id, '', start, <Icon name="arrow-left-right" tone="quiet" />, (
            <>
              {name.final ? <FinalTag /> : null}
              <span className={`truncate group-data-[open=true]/row:text-struct ${name.muted ? 'text-ink2' : 'text-ink1'}`}>{name.text}</span>
            </>
          ), bar({ background: `var(--chart-model-${modelFamily(r.model)})` }, r.model ?? undefined /* FALLBACK-OK: TRN-64 (no model recorded: no tooltip) */), <span className="fig text-ink2">{r.dur_ms === null ? '' : fmt.dur(r.dur_ms)}</span>, { cursor, onClick: open })
        }
        case 'tool': {
          const t = toolName(r)
          const failed = failureText(r) !== null
          return cells(n.id, '', start, <Icon name={t.glyph} tone={t.skill ? 'inherit' : 'quiet'} />, (
            <>
              {failed ? <Icon name="circle-alert" tone="error" label="Failed" /> : null}
              <span className={`truncate group-data-[open=true]/row:text-struct ${t.skill ? 'font-semibold text-ink1' : 'text-ink1'}`}>{t.text}</span>
            </>
          ), bar({ background: 'var(--color-ink2)' }), <span className="fig text-ink2">{durText(r.dur_ms, r.time_prov)}</span>, { cursor, onClick: open })
        }
        case 'agent': {
          const a = agentName(r, p.openAgents.has(n.id))
          return cells(n.id, '', start, <Icon name="bot" tone="inherit" />, (
            <>
              <span className="truncate text-ink1 group-data-[open=true]/row:text-struct">{a.text}</span>
              <span className="shrink-0 text-ink3">{a.rows}</span>
            </>
          ), axis ? (
            <>
              {r.runs.map((run, i) => (
                <Bar key={i} box={barBox(axis, run.start_ms, run.end_ms, MIN_BAR_PX)} style={{ background: 'var(--st-agents)' }} height={BAR_H} title={`${run.agent_name ?? /* FALLBACK-OK: TRN-33 (an unnamed run is called "agent") */ 'agent'}: ${fmt.clock(run.start_ms, p.tz)}–${fmt.clock(run.end_ms, p.tz)}`} />
              ))}
            </>
          ) : null, <span className="fig text-ink2">{fmt.dur(r.run_ms)}</span>, { cursor, onClick: open })
        }
        case 'gap': {
          const g = gapName(r, p.firstTurnIsHuman)
          return cells(n.id, '', start, <Icon name={g.glyph} tone="quiet" />, (
            <>
              <b className="fig shrink-0 font-medium text-ink1">{g.dur}</b>
              <span className="truncate text-ink2">{g.label}</span>
            </>
          ), bar(stateFill(gapState(r)), g.label), <span className="fig text-ink2">{g.dur}</span>, { cursor, onClick: open, className: 'bg-l2', title: `${g.dur} ${g.label}, ${g.reason}` })
        }
      }
    },
    [axis, tailX, grid, p],
  )

  if (p.items.length === 0) return <EmptyState icon="search-x">{p.zoom ? 'No rows in this range.' : 'No rows to draw.'}</EmptyState>
  return (
    <>
      <div data-waterfall-head className="grid h-[76px] shrink-0 items-start border-b border-hair text-ink3" style={{ gridTemplateColumns: grid, columnGap: 12, padding: '8px 20px 0' }}>
        <span>When</span>
        <span />
        <span>{p.rowCount} rows</span>
        <div ref={headRef} className="relative h-[68px] min-w-0">
          <span className="block truncate">{real ? 'Real time' : 'Waits of 60 s or more folded'}</span>
          <AxisHead axis={axis} tz={p.tz} />
          {tailX !== null ? <span className="absolute top-10 whitespace-nowrap text-ink3" style={{ left: Math.min(tailX + 6, trackW - 96) }}>after the turn</span> : null}
        </div>
        <span className="text-right">Time</span>
      </div>
      {axis ? <RowList items={p.items} heightOf={heightOf} render={render} levelOf={itemLevel} cursorIndex={cursorIndex} scroll={p.scroll} label="Rows of the turn on its time axis" /> : <Skeleton height={ROW_H_WF} />}
    </>
  )
}
