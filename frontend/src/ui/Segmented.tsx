/** A segmented control: edged on l1 or l2 (SYS-13), the selected segment on l3 (SYS-1). 32 px, 28 px in the rail (SYS-22). */
export function Segmented<T extends string>({
  options, value, onChange, label, compact = false,
}: { options: readonly { value: T; label: string }[]; value: T; onChange: (v: T) => void; label: string; compact?: boolean }) {
  return (
    <div role="group" aria-label={label} className={`inline-flex overflow-hidden rounded-control border border-edge ${compact ? 'h-7' : 'h-8'}`}>
      {options.map((o, i) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={`px-3 text-body transition-colors duration-150 ease-[var(--ease-standard)] ${i > 0 ? 'border-l border-edge' : ''} ${
              on ? 'bg-l3 font-medium text-ink1' : 'text-ink3 hover:text-struct'
            }`}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
