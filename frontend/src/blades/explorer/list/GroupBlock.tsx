import type { GroupKey } from '../../../api/types'
import { ActivityStrip } from '../../../ui/ActivityStrip'
import { Duration, Money, Tokens } from '../../../ui/figures'
import { FoldRow } from '../../../ui/FoldRow'
import { GroupHeader } from '../../../ui/GroupHeader'
import { FigureCell } from '../../../ui/Row'
import { groupColumns } from '../../../ui/row.logic'
import { groupTitle, type DisplayGroup, type GroupFold } from './list.logic'

const MAX_WALL_MS = 48 * 3600_000

/**
 * EXP-56, SYS-37 (P2): the group header with the group's totals in the row columns and its aggregate activity on the period axis in the
 * Activity column, scaled to the group's own largest bin. Clicking anywhere on the band folds or unfolds the group (EXP-59).
 */
export function GroupBand({ group, mode, open, tz, onToggle }: { group: DisplayGroup; mode: GroupKey; open: boolean; tz: string; onToggle: () => void }) {
  const a = group.period_activity
  const own = Math.max(0, ...a.tok)
  return (
    <div onClick={onToggle} className="cursor-pointer">
      <GroupHeader
        open={open}
        onToggle={() => {}}
        name={groupTitle(group, mode)}
        count={group.n_sessions}
        dot={mode === 'project' && !group.small ? group.color_slot : undefined}
        columns={groupColumns({ magnitude: 200, secondaries: 3 })}
        magnitude={
          <ActivityStrip
            bins={a.tok}
            maxBin={own}
            wallMs={MAX_WALL_MS}
            firstMs={a.start_ms}
            lastMs={a.start_ms + a.tok.length * a.bin_ms}
            tz={tz}
            width={200}
            height={32}
          />
        }
        totals={
          <>
            <FigureCell>
              <Money value={group.cost_usd} lowerBound={group.cost_lower_bound} nUnpriced={group.n_unpriced} weight={600} />
            </FigureCell>
            <FigureCell>
              <Duration ms={group.wall_ms} ink="ink2" />
            </FigureCell>
            <FigureCell>
              <Duration ms={group.active_ms} ink="ink3" />
            </FigureCell>
            <FigureCell>
              <Tokens value={group.tok_total} ink="ink3" />
            </FigureCell>
          </>
        }
      />
    </div>
  )
}

/** EXP-62: in the rail the band is 40 px with the name 14/600 and the group cost. */
export function RailGroupBand({ group, mode, open, onToggle }: { group: DisplayGroup; mode: GroupKey; open: boolean; onToggle: () => void }) {
  return (
    <div onClick={onToggle} className="cursor-pointer">
      <GroupHeader
        compact
        open={open}
        onToggle={() => {}}
        name={groupTitle(group, mode)}
        count={group.n_sessions}
        dot={mode === 'project' && !group.small ? group.color_slot : undefined}
        columns="minmax(0,1fr) auto auto"
        totals={<Money value={group.cost_usd} lowerBound={group.cost_lower_bound} nUnpriced={group.n_unpriced} weight={600} />}
      />
    </div>
  )
}

/** SYS-38: "Show N more · $X". */
export function GroupFoldRow({ fold, expanded, onToggle }: { fold: GroupFold; expanded: boolean; onToggle: () => void }) {
  return <FoldRow label={fold.label} open={expanded} onToggle={onToggle} sum={fold.sum === null ? null : <Money value={fold.sum} />} />
}

/** A page or a group's rows failed to load: the error stays in the place the rows would be (EXP-81). */
export function ErrorRow({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex h-9 items-center gap-3 border-b border-rule-soft px-4 text-body text-ink2">
      <span className="min-w-0 flex-1 truncate">{message}</span>
      <button type="button" onClick={onRetry} className="text-struct hover:underline">
        Retry
      </button>
    </div>
  )
}
