import { useRef, useState } from 'react'
import type { GroupKey, SortDir, SortKey } from '../../../api/types'
import { Icon } from '../../../ui/Icon'
import { Popover } from '../../../ui/Popover'
import { listColumns, listHeads, nextSort, type Head } from './list.logic'

const GROUP_LABEL: Record<'none' | GroupKey, string> = { none: 'None', project: 'Project', day: 'Day' }
const GROUP_OPTIONS: readonly ('none' | GroupKey)[] = ['none', 'project', 'day']

/** EXP-47: the head line. "Sessions" 14/600 ink1, the in-scope count in ink3, and at the right the group control "Group: None". */
export function HeadLine({ total, group, onGroup }: { total: number; group: GroupKey | null; onGroup: (g: GroupKey | null) => void }) {
  const anchor = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const current = group ?? 'none'
  return (
    <div className="mb-3 flex items-center gap-2">
      <h2 className="text-body font-semibold text-ink1">Sessions</h2>
      <span className="fig text-body text-ink3">{total.toLocaleString('en-US')}</span>
      <span className="flex-1" />
      <button
        ref={anchor}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-8 items-center gap-1.5 rounded-control px-2 text-body text-ink2 transition-colors duration-150 hover:bg-l3 hover:text-ink1"
      >
        <Icon name="layers" tone="inherit" />
        <span>Group: {GROUP_LABEL[current]}</span>
        <Icon name="chevron-down" tone="inherit" />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} anchorRef={anchor} width={200} label="Group sessions by">
        <div className="flex flex-col">
          {GROUP_OPTIONS.map((g) => (
            <button
              key={g}
              type="button"
              onClick={() => {
                onGroup(g === 'none' ? null : g)
                setOpen(false)
              }}
              className={`flex h-9 items-center justify-between rounded-control px-3 text-body hover:bg-l4 ${current === g ? 'bg-struct-soft text-struct' : 'text-ink1'}`}
            >
              {GROUP_LABEL[g]}
              {current === g ? <Icon name="check" tone="struct" /> : null}
            </button>
          ))}
        </div>
      </Popover>
    </div>
  )
}

function HeadCell({ head, sort, dir, onSort }: { head: Head; sort: SortKey; dir: SortDir; onSort: (k: SortKey) => void }) {
  const justify = head.align === 'right' ? 'justify-end' : 'justify-start'
  if (head.sort === null) return <span className={`flex items-center truncate text-body font-medium text-ink3 ${justify}`}>{head.label}</span>
  const active = head.sort === sort
  const key = head.sort
  return (
    <button
      type="button"
      aria-sort={active ? (dir === 'desc' ? 'descending' : 'ascending') : 'none'}
      onClick={() => onSort(key)}
      className={`flex items-center gap-1 text-body font-medium transition-colors duration-150 hover:text-ink1 ${justify} ${active ? 'text-ink1' : 'text-ink3'}`}
    >
      {head.label}
      {active ? <Icon name={dir === 'desc' ? 'arrow-down-narrow-wide' : 'arrow-up-narrow-wide'} tone="inherit" /> : null}
    </button>
  )
}

/** EXP-50: the sticky column band: l2, 1 px rule border, 8 px radius, 40 px tall. Only the active sort head shows an arrow. */
export function ColumnBand({ group, sort, dir, onSort }: { group: GroupKey | null; sort: SortKey; dir: SortDir; onSort: (next: { sort: SortKey; dir: SortDir }) => void }) {
  return (
    <div
      className="sticky top-0 z-20 grid h-10 items-center gap-x-4 rounded-[8px] border border-rule bg-l2 px-4"
      style={{ gridTemplateColumns: listColumns(group) }}
    >
      {listHeads(group).map((h) => (
        <HeadCell key={h.id} head={h} sort={sort} dir={dir} onSort={(k) => onSort(nextSort({ sort, dir }, k))} />
      ))}
    </div>
  )
}
