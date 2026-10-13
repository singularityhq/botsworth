import { useCallback, useMemo } from 'react'
import type { Bars } from '../../state/url'
import { EmptyState } from '../../ui/EmptyState'
import { Money } from '../../ui/figures'
import { SkeletonRows } from '../../ui/Skeleton'
import { BandError, BandSkeleton, ShowMore } from './AgentBand'
import { AgentRowView } from './AgentRow'
import { CallRowView } from './CallRow'
import { GapRowTurn } from './GapRowTurn'
import { QuietFold } from './QuietFold'
import { RowList, type ScrollRequest } from './RowList'
import type { RowEnv } from './rowKit'
import type { AgentRange, Item } from './rowList.logic'
import { ToolRowView } from './ToolRow'
import { cursorIndexOf, itemHeight, itemLevel, ROW_H, type LedgerGrid } from './turn.logic'

const BAR_NAME = { cost: 'cost', time: 'time', tokens: 'tokens' } as const

/** TRN-11: the column header, 32 px, ink3, hairline below. */
function ColumnHead({ grid, order, measure }: { grid: LedgerGrid; order: 'cost' | 'duration' | null; measure: Bars }) {
  const cell: Record<string, React.ReactNode> = {
    when: 'When',
    name: order === null ? 'As it ran' : order === 'cost' ? 'Top by cost' : 'Top by duration',
    bar: <span className="block text-right">by {BAR_NAME[measure]}</span>,
    cost: <span className="block text-right">Cost</span>,
    time: <span className="block text-right">Time</span>,
    tokens: <span className="block text-right">Tokens</span>,
  }
  return (
    <div className="grid h-8 shrink-0 items-center border-b border-hair text-ink3" style={{ gridTemplateColumns: grid.template, columnGap: 12, padding: '0 20px' }}>
      {grid.ids.map((id) => (
        <span key={id} className="truncate">{cell[id] ?? /* FALLBACK-OK: TRN-11 (a column with no heading is blank) */ null}</span>
      ))}
    </div>
  )
}

export interface LedgerProps {
  items: readonly Item[]
  /** The turn has no rows at all (TRN-62). */
  turnEmpty: boolean
  loading: boolean
  search: string
  zoomed: boolean
  order: 'cost' | 'duration' | null
  env: RowEnv
  toolNames: ReadonlyMap<string, readonly string[]>
  rangeAgents?: ReadonlyMap<string, AgentRange>
  openAgents: ReadonlySet<string>
  firstTurnIsHuman: boolean
  maxGapMs: number
  scroll: ScrollRequest
  onToggleAgent: (id: string) => void
  onUnfold: (foldKey: string) => void
  onShowAll: () => void
  onBandMore: (agent: string) => void
  onBandRetry: (agent: string) => void
}

/** TRN-11, TRN-30 to TRN-37: the Ledger — column header and the windowed list of call, tool, agent and gap rows, folds and band items. */
export function Ledger(p: LedgerProps) {
  const { env } = p
  const cursorIndex = useMemo(() => cursorIndexOf(p.items, env.cursorId), [p.items, env.cursorId])
  const render = useCallback(
    (it: Item): React.ReactNode => {
      switch (it.type) {
        case 'row': {
          const n = it.node
          switch (n.row.type) {
            case 'call':
              return <CallRowView node={n} env={env} toolNames={p.toolNames.get(n.id) ?? []} /> // FALLBACK-OK: TRN-31 (a call whose tool rows are not in the list names itself from its text)
            case 'tool':
              return <ToolRowView node={n} env={env} />
            case 'agent':
              return <AgentRowView node={n} env={env} open={p.openAgents.has(n.id)} expandable={p.order === null} range={p.rangeAgents?.get(n.id)} onToggle={() => p.onToggleAgent(n.id)} />
            case 'gap':
              return <GapRowTurn node={n} env={env} firstTurnIsHuman={p.firstTurnIsHuman} maxMs={p.maxGapMs} />
          }
          return null
        }
        case 'fold':
          return <QuietFold item={it} tz={env.tz} grid={env.grid} onUnfold={() => p.onUnfold(it.key)} />
        case 'more':
          return it.kind === 'order' ? (
            <ShowMore n={it.n} cost={<Money value={it.cost} weight={400} />} onMore={p.onShowAll} />
          ) : (
            <ShowMore n={it.n} onMore={() => p.onBandMore(it.agent)} />
          )
        case 'band':
          return it.status === 'loading' ? <BandSkeleton /> : <BandError message={it.message} onRetry={() => p.onBandRetry(it.agent)} />
      }
    },
    [env, p],
  )

  if (p.loading) return <><ColumnHead grid={env.grid} order={p.order} measure={env.measure} /><SkeletonRows count={10} rowHeight={ROW_H} /></>
  if (p.turnEmpty) return <EmptyState>No model calls in this turn.</EmptyState>
  if (p.items.length === 0) {
    return <EmptyState icon="search-x">{p.search !== '' ? `No rows match “${p.search}”.` : p.zoomed ? 'No rows in this range.' : 'No model calls in this turn.'}</EmptyState>
  }
  return (
    <>
      <ColumnHead grid={env.grid} order={p.order} measure={env.measure} />
      <RowList items={p.items} heightOf={itemHeight} render={render} levelOf={itemLevel} cursorIndex={cursorIndex} scroll={p.scroll} label="Rows of the turn" />
    </>
  )
}
