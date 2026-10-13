import { Icon } from './Icon'

/** SYS-38: the fold row, 36 px. "Show N more" in struct with the hidden rows' sum right-aligned; once open it reads "Show top N" with no sum. */
export function FoldRow({ label, open, onToggle, sum }: { label: string; open: boolean; onToggle: () => void; sum?: React.ReactNode }) {
  return (
    <button type="button" aria-expanded={open} onClick={onToggle} className="flex h-9 w-full items-center gap-1.5 border-b border-rule-soft px-4 text-body text-struct hover:bg-l3">
      <Icon name="chevron-right" tone="struct" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
      <span className="flex-1">{label}</span>
      {open ? null : sum}
    </button>
  )
}
