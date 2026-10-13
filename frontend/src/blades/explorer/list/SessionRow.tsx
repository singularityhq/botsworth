import type { GroupKey, SessionRow } from '../../../api/types'
import { ActivityStrip } from '../../../ui/ActivityStrip'
import { Clock, Duration, Money, Tokens } from '../../../ui/figures'
import { Icon } from '../../../ui/Icon'
import { MagnitudeBar } from '../../../ui/MagnitudeBar'
import { ProjectDot } from '../../../ui/ProjectDot'
import { ProviderMark } from '../../../ui/ProviderMark'
import { FigureCell, Row, WhenCell } from '../../../ui/Row'
import { seriesVar } from '../../../ui/series'
import { Skeleton } from '../../../ui/Skeleton'
import { Tooltip } from '../../../ui/Tooltip'
import { costWeight, flagsFor, showProjectColumn, type Flag } from './list.logic'

/** What every row of one list shares. */
export interface RowCtx {
  tz: string
  /** SYS-34: under a single-day period no weekday shows. */
  singleDay: boolean
  columns: string
  group: GroupKey | null
  /** SYS-39 bar scale. */
  maxCost: number
  /** EXP-49: the cost weight threshold. */
  p90: number | null
  /** SYS-40: the shared strip scale. */
  maxActivity: number
}

export function FlagMark({ flag }: { flag: Flag }) {
  switch (flag.kind) {
    case 'provider':
      return (
        <Tooltip content={flag.tooltip}>
          <ProviderMark provider={flag.provider} />
        </Tooltip>
      )
    case 'automated':
      return (
        <Tooltip content={flag.tooltip}>
          <Icon name="repeat" tone="muted" label={flag.tooltip} />
        </Tooltip>
      )
    case 'agents':
      return (
        <Tooltip content={flag.tooltip}>
          <span className="inline-flex items-center gap-1 text-ink3">
            <Icon name="bot" tone="muted" />
            <span className="fig">{flag.n}</span>
          </span>
        </Tooltip>
      )
    case 'nights':
      return (
        <Tooltip content={flag.tooltip}>
          <span className="fig text-ink2">+{flag.n}d</span>
        </Tooltip>
      )
  }
}

/** SYS-30: levels 1 to 3 are 14/500 ink1; the fallback `project · id8` is `circle-dashed` and mono 14/400 ink3, so it reads as unnamed. */
export function SessionName({ row }: { row: SessionRow }) {
  const unnamed = row.title_source === 'fallback'
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      {unnamed ? <Icon name="circle-dashed" tone="muted" /> : null}
      <span
        title={row.name}
        className={`truncate ${unnamed ? 'font-mono font-normal text-ink3' : 'font-medium text-ink1 group-data-[open=true]/row:text-struct'}`}
      >
        {row.name}
      </span>
    </span>
  )
}

/** EXP-49: the project's dot and name in its series colour, 14/500, ellipsis (D32). */
export function ProjectCell({ row }: { row: SessionRow }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <ProjectDot slot={row.color_slot} />
      <span title={row.project_name} className="truncate font-medium" style={{ color: seriesVar(row.color_slot) }}>
        {row.project_name}
      </span>
    </span>
  )
}

interface ListRowProps {
  row: SessionRow
  prev: SessionRow | null
  ctx: RowCtx
  open: boolean
  cursor: boolean
  onOpen: (row: SessionRow) => void
  onHover: (row: SessionRow | null) => void
}

/** EXP-49: one session, 44 px, in the SYS-33 grid. */
export function ListRow({ row, prev, ctx, open, cursor, onOpen, onHover }: ListRowProps) {
  const flags = flagsFor(row, ctx.tz)
  return (
    <Row
      columns={ctx.columns}
      open={open}
      cursor={cursor}
      role="listitem"
      aria-current={open || undefined}
      data-session={row.session}
      onClick={() => onOpen(row)}
      onPointerEnter={() => onHover(row)}
      onPointerLeave={() => onHover(null)}
    >
      <WhenCell
        startMs={row.start_ms}
        prevStartMs={prev ? prev.start_ms : null}
        tz={ctx.tz}
        singleDay={ctx.singleDay}
        clock={<Clock ms={row.start_ms} tz={ctx.tz} lead />}
      />
      {showProjectColumn(ctx.group) ? <ProjectCell row={row} /> : null}
      <SessionName row={row} />
      <span className="flex items-center gap-2">
        {flags.map((f) => (
          <FlagMark key={f.kind} flag={f} />
        ))}
      </span>
      <ActivityStrip
        bins={row.activity.tok}
        maxBin={ctx.maxActivity}
        wallMs={row.wall_ms}
        firstMs={row.start_ms}
        lastMs={row.end_ms}
        tz={ctx.tz}
        width={200}
      />
      <FigureCell>
        <MagnitudeBar value={row.cost_usd} max={ctx.maxCost} track={96} />
        <Money value={row.cost_usd} lowerBound={row.cost_lower_bound} nUnpriced={row.n_unpriced} weight={costWeight(row.cost_usd, ctx.p90)} />
      </FigureCell>
      <FigureCell>
        <Duration ms={row.wall_ms} ink="ink2" />
      </FigureCell>
      <FigureCell>
        <Duration ms={row.active_ms} ink="ink3" />
      </FigureCell>
      <FigureCell>
        <Tokens value={row.tok_total} ink="ink3" />
      </FigureCell>
    </Row>
  )
}

/** A row whose page has not loaded (SYS-54): l2 fill at the row's height. */
export function SkeletonListRow({ height }: { height: number }) {
  return (
    <div role="presentation" className="flex items-center border-b border-rule-soft px-4" style={{ height }}>
      <Skeleton height={14} width="40%" />
    </div>
  )
}
