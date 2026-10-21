import type { AnyRow } from '../../api/types'
import type { Bars } from '../../state/url'
import { Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import type { IconName } from '../../ui/glyphs'
import { Row } from '../../ui/Row'
import { MeasureBar } from '../session/TurnRow'
import { clockSec, isBig, type LedgerGrid } from './turn.logic'
import { fmt } from '../../lib/format'
import { ModelMark } from '../../ui/ModelMark'
import { Tooltip } from '../../ui/Tooltip'
import { DERIVED_TIP, durText } from './rowName'
import type { RowNode } from './rowList.logic'

/** What every row component gets from the list: how to draw and how to react. */
export interface RowEnv {
  tz: string
  grid: LedgerGrid
  /** The measure the bar and the `.big` rule follow (TRN-11). */
  measure: Bars
  /** The list's largest value per measure (TRN-31 `.big`, SYS-39 bar scale). */
  max: Record<Bars, number>
  cursorId: string | null
  /** Click on a row: make it the cursor and open the detail (TRN-36). */
  onOpen: (id: string) => void
}

/** The ledger row frame: one grid line, 36 px (SYS-22 dense), cursor = struct-soft fill + struct name + outline (TRN-30). */
export function LedgerFrame({
  env, node, cells, className = '', title,
}: { env: RowEnv; node: RowNode; cells: Partial<Record<keyof LedgerCells, React.ReactNode>>; className?: string; title?: string }) {
  const cursor = env.cursorId === node.id
  return (
    <Row
      dense
      columns={env.grid.template}
      open={cursor}
      cursor={cursor}
      data-row-id={node.id}
      data-depth={node.depth}
      role="button"
      tabIndex={-1}
      aria-current={cursor || undefined}
      title={title}
      className={className}
      onClick={() => env.onOpen(node.id)}
      style={{ columnGap: 12, padding: '0 20px' }}
    >
      {env.grid.ids.map((id) => (
        <Cell key={id} id={id} cells={cells} />
      ))}
    </Row>
  )
}

type LedgerCells = Record<'when' | 'glyph' | 'name' | 'flags' | 'bar' | 'cost' | 'time' | 'tokens', React.ReactNode>

function Cell({ id, cells }: { id: keyof LedgerCells; cells: Partial<Record<keyof LedgerCells, React.ReactNode>> }) {
  const content = cells[id]
  const right = id === 'cost' || id === 'time' || id === 'tokens'
  return <span className={right ? 'flex min-w-0 items-center justify-end' : id === 'name' ? 'flex min-w-0 items-baseline gap-2 whitespace-nowrap' : 'flex min-w-0 items-center'}>{content}</span>
}

/** TRN-30: the start clock, mono ink3, with seconds. */
export function When({ ms, tz }: { ms: number | null; tz: string }) {
  return ms === null ? null : <span className="fig text-ink3">{clockSec(ms, tz)}</span>
}

export function Glyph({ name, tone = 'quiet', label }: { name: IconName; tone?: 'quiet' | 'muted' | 'struct' | 'error' | 'inherit'; label?: string }) {
  return <Icon name={name} tone={tone} label={label} />
}

/** The model mark in its compact form, or the "other" mark with the tooltip "Model not recorded" (TRN-64, DET-61). */
export function ModelFlag({ model }: { model: string | null }) {
  if (model === null || model === '') {
    return (
      <Tooltip content="Model not recorded">
        <span className="inline-flex"><ModelMark model="" compact /></span>
      </Tooltip>
    )
  }
  return <ModelMark model={model} compact />
}

/** TRN-31: the active measure's figure is 600 when it is at least 25 % of the list's largest. Money stays money; the others are ink1. */
export function CostCell({ value, lowerBound, nUnpriced, env }: { value: number | null; lowerBound: boolean; nUnpriced: number; env: RowEnv }) {
  const big = env.measure === 'cost' && isBig(value, env.max.cost)
  return <Money value={value} lowerBound={lowerBound} nUnpriced={nUnpriced} weight={big ? 600 : 400} />
}

export function TimeCell({ ms, env, prov }: { ms: number | null; env: RowEnv; prov?: 'present' | 'derived' | 'estimated' | 'missing' }) {
  if (ms === null) return null
  const big = env.measure === 'time' && isBig(ms, env.max.time)
  if (prov === 'derived') {
    return (
      <Tooltip content={DERIVED_TIP}>
        <span className={`fig whitespace-nowrap ${big ? 'font-semibold text-ink1' : 'text-ink2'}`}>{durText(ms, prov)}</span>
      </Tooltip>
    )
  }
  return <Duration ms={ms} ink={big ? 'ink1' : 'ink2'} className={big ? 'font-semibold' : ''} />
}

/** TRN-31: Tokens, `—` when a model call's tokens are null (S2 §5); blank for rows that have none by construction. */
export function TokensCell({ value, env, call = false }: { value: number | null; env: RowEnv; call?: boolean }) {
  if (value === null) return call ? <span className="fig text-ink3">—</span> : null
  const big = env.measure === 'tokens' && isBig(value, env.max.tokens)
  return <span className={`fig whitespace-nowrap ${big ? 'font-semibold text-ink1' : 'text-ink3'}`}>{fmt.tokens(value)}</span>
}

/** SYS-39: the measure bar in its 104 px track; the bar colour follows the measure (cost `--list-bar`, others ink2). */
export function BarCell({ row, env, value }: { row: AnyRow; env: RowEnv; value: number | null }) {
  void row
  return <MeasureBar value={value ?? 0 /* FALLBACK-OK: SYS-39 (no figure: no bar) */} max={env.max[env.measure]} bars={env.measure} />
}

/** TRN-32: `circle-alert` and the failure text in `--color-error` (SYS-8). */
export function ErrorFlag({ text }: { text: string }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-error">
      <Icon name="circle-alert" tone="error" />
      {text}
    </span>
  )
}

/** TRN-32: an ink3 `hourglass` flag ("no records 6m", "asked you"). */
export function QuietFlag({ text }: { text: string }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-ink3">
      <Icon name="hourglass" tone="muted" />
      {text}
    </span>
  )
}
