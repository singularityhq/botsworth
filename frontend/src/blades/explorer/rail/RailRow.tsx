import type { GroupKey, SessionRow } from '../../../api/types'
import { ActivityStrip } from '../../../ui/ActivityStrip'
import { Clock, Money } from '../../../ui/figures'
import { ProjectDot } from '../../../ui/ProjectDot'
import { CompactRow, WhenCell } from '../../../ui/Row'
import { seriesVar } from '../../../ui/series'
import { Skeleton } from '../../../ui/Skeleton'
import { costWeight } from '../list/list.logic'
import { RAIL_METRICS } from '../list/virtual.logic'

export interface RailCtx {
  tz: string
  singleDay: boolean
  group: GroupKey | null
  p90: number | null
  maxActivity: number
}

interface RailRowProps {
  row: SessionRow
  prev: SessionRow | null
  ctx: RailCtx
  open: boolean
  cursor: boolean
  onSelect: (row: SessionRow) => void
  onHover: (row: SessionRow | null) => void
}

/** EXP-62: two lines. Line 1: the name and the cost (600 in the top 10 %). Line 2: when, the project (not when grouped by project), the activity strip 88 x 16. */
export function RailRow({ row, prev, ctx, open, cursor, onSelect, onHover }: RailRowProps) {
  const unnamed = row.title_source === 'fallback'
  return (
    <CompactRow
      open={open}
      cursor={cursor}
      role="listitem"
      aria-current={open || undefined}
      data-session={row.session}
      style={{ height: RAIL_METRICS.row }}
      onClick={() => onSelect(row)}
      onPointerEnter={() => onHover(row)}
      onPointerLeave={() => onHover(null)}
      name={unnamed ? <span className="font-mono font-normal text-ink3">{row.name}</span> : row.name}
      nameTitle={row.name}
      figure={<Money value={row.cost_usd} lowerBound={row.cost_lower_bound} nUnpriced={row.n_unpriced} weight={costWeight(row.cost_usd, ctx.p90)} />}
      when={
        <WhenCell
          startMs={row.start_ms}
          prevStartMs={prev ? prev.start_ms : null}
          tz={ctx.tz}
          singleDay={ctx.singleDay}
          clock={<Clock ms={row.start_ms} tz={ctx.tz} />}
        />
      }
      meta={
        ctx.group === 'project' ? null : (
          <span className="flex min-w-0 items-center gap-1.5">
            <ProjectDot slot={row.color_slot} />
            <span title={row.project_name} className="truncate font-medium" style={{ color: seriesVar(row.color_slot) }}>
              {row.project_name}
            </span>
          </span>
        )
      }
      mark={
        <ActivityStrip
          bins={row.activity.tok}
          maxBin={ctx.maxActivity}
          wallMs={row.wall_ms}
          firstMs={row.start_ms}
          lastMs={row.end_ms}
          tz={ctx.tz}
          width={88}
          height={16}
        />
      }
    />
  )
}

export function SkeletonRailRow() {
  return (
    <div role="presentation" className="border-b border-rule-soft p-2" style={{ height: RAIL_METRICS.row }}>
      <Skeleton height={14} width="60%" />
      <div className="mt-2">
        <Skeleton height={12} width="35%" />
      </div>
    </div>
  )
}
