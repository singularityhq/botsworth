import { Icon } from './Icon'
import type { IconName } from './glyphs'
import { ROW_H, ROW_H_DENSE, weekdayLabel } from './row.logic'

interface RowProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Grid template from `rowColumns`. */
  columns: string
  dense?: boolean
  /** SYS-35: the row's blade is showing. */
  open?: boolean
  /** SYS-35: the keyboard cursor is on this row. */
  cursor?: boolean
  children: React.ReactNode
}

/** SYS-33, SYS-35 (P1): one line, 44 px (36 dense), fixed slot order. The caller fills the slots in order. */
export function Row({ columns, dense = false, open = false, cursor = false, className = '', style, children, ...rest }: RowProps) {
  return (
    <div
      {...rest}
      data-open={open}
      data-cursor={cursor}
      className={`group/row grid cursor-pointer items-center gap-x-4 border-b border-rule-soft px-4 text-body transition-colors duration-150 ease-[var(--ease-standard)] hover:bg-l3 focus-visible:outline-offset-[-2px] data-[open=true]:bg-struct-soft data-[open=true]:hover:bg-struct-soft data-[cursor=true]:outline-2 data-[cursor=true]:-outline-offset-2 data-[cursor=true]:outline-struct ${className}`}
      style={{ ...style, gridTemplateColumns: columns, height: dense ? ROW_H_DENSE : ROW_H }}
    >
      {children}
    </div>
  )
}

/** Slot 1: weekday (only when the day changes, SYS-34) and the start clock. */
export function WhenCell({ startMs, prevStartMs, tz, singleDay, clock }: { startMs: number; prevStartMs: number | null; tz: string; singleDay: boolean; clock: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-2 whitespace-nowrap">
      <span className="w-8 text-ink3">{weekdayLabel(startMs, prevStartMs, tz, singleDay)}</span>
      {clock}
    </span>
  )
}

/** Slot 2: an optional 16 px kind glyph, then the name on one line. The name is the lead (ink1 500); in struct while the row is open. */
export function RowName({ icon, children, title }: { icon?: IconName; children: React.ReactNode; title?: string }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      {icon ? <Icon name={icon} tone="quiet" /> : null}
      <span title={title} className="truncate font-medium text-ink1 group-data-[open=true]/row:text-struct">
        {children}
      </span>
    </span>
  )
}

/** A right-aligned figure cell (slots 5 and 6+). */
export function FigureCell({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <span className={`flex items-center justify-end gap-2 ${className}`}>{children}</span>
}

interface CompactRowProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'children' | 'title'> {
  open?: boolean
  cursor?: boolean
  name: React.ReactNode
  nameTitle?: string
  figure: React.ReactNode
  when: React.ReactNode
  meta?: React.ReactNode
  mark?: React.ReactNode
}

/** SYS-36: the compact form for parent blades. Line 1: name and primary figure. Line 2: When, meta, a spacer, the magnitude mark. */
export function CompactRow({ open = false, cursor = false, name, nameTitle, figure, when, meta, mark, className = '', ...rest }: CompactRowProps) {
  return (
    <div
      {...rest}
      data-open={open}
      data-cursor={cursor}
      className={`group/row cursor-pointer border-b border-rule-soft p-2 text-body transition-colors duration-150 ease-[var(--ease-standard)] hover:bg-l3 focus-visible:outline-offset-[-2px] data-[open=true]:bg-struct-soft data-[open=true]:hover:bg-struct-soft data-[cursor=true]:outline-2 data-[cursor=true]:-outline-offset-2 data-[cursor=true]:outline-struct ${className}`}
    >
      <div className="flex items-center gap-2">
        <span title={nameTitle} className="min-w-0 flex-1 truncate font-medium text-ink1 group-data-[open=true]/row:text-struct">
          {name}
        </span>
        {figure}
      </div>
      <div className="mt-1 flex items-center gap-2 text-ink3">
        {when}
        {meta}
        <span className="flex-1" />
        {mark}
      </div>
    </div>
  )
}
