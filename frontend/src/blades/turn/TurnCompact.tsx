import { useCallback, useMemo } from 'react'
import type { TurnRow, TurnRows } from '../../api/types'
import { fmt } from '../../lib/format'
import { Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { ModelMark } from '../../ui/ModelMark'
import { CompactRow } from '../../ui/Row'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import { kindOf } from '../shared/states'
import { BandError, BandSkeleton, ShowMore } from './AgentBand'
import { RowList, type ScrollRequest } from './RowList'
import { ErrorFlag } from './rowKit'
import { agentName, callName, durText, failureText, gapName, toolName } from './rowName'
import { rowStart, type AgentRange, type Item } from './rowList.logic'
import { costFromRow, cursorIndexOf, itemHeight, itemLevel, clockSec } from './turn.logic'

/** TRN-1: the two-line row is this tall (two 20 px lines, 4 px between, 8 px padding, the hairline). */
export const COMPACT_ROW_H = 62

const KIND_TINT = { message: 'text-type-user', slash: 'text-type-user', resume: 'text-type-user', agent: 'text-type-team', plugin: 'text-type-sys', scripted: 'text-type-sys', harness: '' } as const

interface CompactHeaderProps {
  row: TurnRow | undefined
  turn: TurnRows['turn'] | undefined
  ix: number
  total: number
  isFirst: boolean
  isLast: boolean
  onPrev: () => void
  onNext: () => void
  onClose: () => void
  onOpenMessage: () => void
}

function IconButton({ label, icon, disabled = false, onClick }: { label: string; icon: 'chevron-left' | 'chevron-right' | 'x'; disabled?: boolean; onClick: () => void }) {
  return (
    <Tooltip content={label}>
      <button
        type="button"
        aria-label={label.replace(/ \(.\)/, '')}
        disabled={disabled}
        onClick={onClick}
        className="inline-flex h-7 w-7 items-center justify-center rounded-control text-ink3 transition-colors duration-150 hover:bg-l3 hover:text-struct disabled:cursor-default disabled:text-edge disabled:hover:bg-transparent disabled:hover:text-edge"
      >
        <Icon name={icon} tone="inherit" />
      </button>
    </Tooltip>
  )
}

/** TRN-1, the compact form at 400 px: the meta line, the title, and the cost and wall figures with no split bars or legend. */
export function CompactHeader(p: CompactHeaderProps) {
  const kind = p.row ? kindOf(p.row.kind, p.row.origin, p.row.label) : null
  const cost = p.turn ? p.turn.cost.cost_usd : p.row ? costFromRow(p.row).cost_usd : undefined
  const lower = p.turn ? p.turn.cost.cost_lower_bound : (p.row?.cost_lower_bound ?? false) // FALLBACK-OK: TRN-60 (no row yet: nothing to flag)
  const wall = p.turn ? p.turn.wall_ms : (p.row?.wall_ms ?? null) // FALLBACK-OK: TRN-60 (no row yet: the wall shows its skeleton)
  return (
    <div className="shrink-0 px-4 pb-2 pt-3">
      <div className="flex min-h-7 items-center gap-2 whitespace-nowrap text-ink3">
        {kind ? <Icon name={kind.icon} tone="inherit" label={kind.label} className={KIND_TINT[kind.key]} /> : null}
        <span className="text-ink2">
          Turn {p.ix} of {p.total}
        </span>
        <span className="flex-1" />
        <IconButton label="Previous turn (T)" icon="chevron-left" disabled={p.isFirst} onClick={p.onPrev} />
        <IconButton label="Next turn (t)" icon="chevron-right" disabled={p.isLast} onClick={p.onNext} />
        <IconButton label="Close (Esc)" icon="x" onClick={p.onClose} />
      </div>
      <h1 className="mb-2 mt-0.5 min-w-0 text-lead font-semibold text-ink1">
        {p.row ? (
          <button type="button" onClick={p.onOpenMessage} title={p.row.label} className="block max-w-full truncate text-left hover:text-struct">
            {p.row.label}
          </button>
        ) : (
          <Skeleton width={240} height={22} />
        )}
      </h1>
      <div className="flex items-baseline gap-5">
        {cost === undefined ? (
          <Skeleton width={96} height={36} />
        ) : cost === null ? (
          <span className="fig text-display font-medium text-ink3">—</span>
        ) : (
          <Money value={cost} lowerBound={lower} nUnpriced={p.turn?.cost.n_unpriced ?? p.row?.n_unpriced ?? 0} className="text-display" /> // FALLBACK-OK: TRN-60 (the unpriced count shows once the turn's rows arrive)
        )}
        <span className="inline-flex items-baseline gap-2">
          {wall === null && p.turn === undefined ? <Skeleton width={72} height={36} /> : <span className="fig text-display font-medium text-ink1">{fmt.dur(wall ?? 0 /* FALLBACK-OK: SES-85 (a turn with no span shows a wall of zero) */)}</span>}
          <span className="text-ink3">wall</span>
        </span>
      </div>
    </div>
  )
}

interface CompactListProps {
  items: readonly Item[]
  tz: string
  cursorId: string | null
  toolNames: ReadonlyMap<string, readonly string[]>
  openAgents: ReadonlySet<string>
  rangeAgents?: ReadonlyMap<string, AgentRange>
  firstTurnIsHuman: boolean
  scroll: ScrollRequest
  onOpen: (id: string) => void
  onUnfold: (foldKey: string) => void
  onShowAll: () => void
  onBandMore: (agent: string) => void
  onBandRetry: (agent: string) => void
}

/** TRN-1: two-line rows. Line 1: glyph, name, primary figure (cost, else time). Line 2: When, Time, flags. */
export function CompactList(p: CompactListProps) {
  const cursorIndex = useMemo(() => cursorIndexOf(p.items, p.cursorId), [p.items, p.cursorId])
  const heightOf = useCallback((it: Item) => itemHeight(it, COMPACT_ROW_H), [])
  const render = useCallback(
    (it: Item): React.ReactNode => {
      if (it.type === 'more') return it.kind === 'order' ? <ShowMore n={it.n} cost={<Money value={it.cost} weight={400} />} onMore={p.onShowAll} /> : <ShowMore n={it.n} onMore={() => p.onBandMore(it.agent)} />
      if (it.type === 'band') return it.status === 'loading' ? <BandSkeleton /> : <BandError message={it.message} onRetry={() => p.onBandRetry(it.agent)} />
      if (it.type === 'fold') {
        return <ShowMore n={it.nodes.length} label={`Show ${it.nodes.length} quiet rows`} cost={<Money value={it.cost} weight={400} />} onMore={() => p.onUnfold(it.key)} />
      }
      const n = it.node
      const r = n.row
      const cursor = p.cursorId === n.id
      const start = rowStart(r)
      const when = start === null ? null : <span className="fig">{clockSec(start, p.tz)}</span>
      let glyph: React.ReactNode
      let name: string
      let figure: React.ReactNode
      let meta: React.ReactNode = null
      switch (r.type) {
        case 'call': {
          const c = callName(r, p.toolNames.get(n.id) ?? []) // FALLBACK-OK: TRN-31 (a call whose tool rows are not listed is named from its text)
          glyph = <Icon name="arrow-left-right" tone="quiet" />
          name = c.text
          figure = <Money value={r.cost_usd} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} weight={500} />
          meta = (
            <>
              <Duration ms={r.dur_ms} />
              {r.model ? <ModelMark model={r.model} compact /> : null}
            </>
          )
          break
        }
        case 'tool': {
          const t = toolName(r)
          const fail = failureText(r)
          glyph = <Icon name={t.glyph} tone={t.skill ? 'inherit' : 'quiet'} />
          name = t.text
          figure = <span className="fig text-ink2">{durText(r.dur_ms, r.time_prov)}</span>
          meta = fail ? <ErrorFlag text={fail} /> : t.sub ? <span>{t.sub}</span> : null
          break
        }
        case 'agent': {
          const a = agentName(r, p.openAgents.has(n.id), p.rangeAgents?.get(n.id))
          glyph = <Icon name="bot" tone="inherit" />
          name = a.text
          figure = <Money value={p.rangeAgents?.get(n.id)?.cost_usd ?? /* FALLBACK-OK: TRN-33 (outside a zoom the whole engagement cost) */ r.cost_usd} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} weight={500} />
          meta = (
            <>
              <Duration ms={r.run_ms} />
              <span>{a.rows}</span>
            </>
          )
          break
        }
        case 'gap': {
          const g = gapName(r, p.firstTurnIsHuman)
          glyph = <Icon name={g.glyph} tone="quiet" />
          name = `${g.dur} ${g.label}`
          figure = <Duration ms={r.dur_ms} />
          break
        }
      }
      return (
        <CompactRow
          open={cursor}
          cursor={cursor}
          data-row-id={n.id}
          role="button"
          tabIndex={-1}
          aria-current={cursor || undefined}
          className={`box-border overflow-hidden ${r.type === 'gap' ? 'bg-l2' : ''}`}
          style={{ height: COMPACT_ROW_H }}
          onClick={() => p.onOpen(n.id)}
          name={
            <span className="inline-flex min-w-0 items-center gap-1.5">
              {glyph}
              <span className="truncate">{name}</span>
            </span>
          }
          nameTitle={name}
          figure={figure}
          when={when}
          meta={meta}
        />
      )
    },
    [p],
  )
  return <RowList items={p.items} heightOf={heightOf} render={render} levelOf={itemLevel} cursorIndex={cursorIndex} scroll={p.scroll} label="Rows of the turn" />
}
