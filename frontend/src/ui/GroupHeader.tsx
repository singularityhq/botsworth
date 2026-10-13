import { Icon } from './Icon'
import { ProjectDot } from './ProjectDot'
import type { Slot } from './series'

interface GroupHeaderProps {
  open: boolean
  onToggle: () => void
  name: string
  count: number
  /** Project groups show the series dot (10 px); other groups pass undefined. */
  dot?: Slot
  /** The rail form: 40 px band, 14/600 name. */
  compact?: boolean
  /** Grid template from `groupColumns`; omit when the header has no totals columns. */
  columns?: string
  /** Magnitude slot content (the group's aggregate activity strip). */
  magnitude?: React.ReactNode
  /** The group's totals, one node per remaining column (primary, then secondaries). */
  totals?: React.ReactNode
}

/** SYS-37 (P2): a filled l2 band its rows hang from. Sticky at the top of the scroll container. */
export function GroupHeader({ open, onToggle, name, count, dot, compact = false, columns, magnitude, totals }: GroupHeaderProps) {
  return (
    <div
      className={`sticky top-0 z-10 grid items-center gap-x-4 rounded-t-[8px] border-y border-rule bg-l2 px-4 ${compact ? 'h-10' : 'h-[52px]'}`}
      // FALLBACK-OK: SYS-37 (a header with no totals columns is a single column)
      style={{ gridTemplateColumns: columns ?? 'minmax(0,1fr)' }}
    >
      <button type="button" aria-expanded={open} onClick={onToggle} className="flex min-w-0 items-center gap-1.5">
        <Icon name="chevron-right" tone="quiet" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
        {dot !== undefined ? <ProjectDot slot={dot} size={10} /> : null}
        <span className={`truncate font-semibold text-ink1 ${compact ? 'text-body' : 'text-lead'}`}>{name}</span>
        <span className="text-body font-normal text-ink3">{count}</span>
      </button>
      {columns ? (
        <>
          <div className="flex items-center">{magnitude}</div>
          {totals}
        </>
      ) : null}
    </div>
  )
}

/** The container the group's rows sit in: a rule border on left, right and bottom, an 8 px bottom radius, no indent. */
export function GroupBody({ children }: { children: React.ReactNode }) {
  return <div className="overflow-hidden rounded-b-[8px] border-x border-b border-rule">{children}</div>
}
