import { useMemo, useState } from 'react'
import { Icon } from '../../ui/Icon'
import { outputView } from './detail.logic'

/**
 * DET-24: a tool's output in an l2 mono well: the first 20 lines, a clickable divider "… N more lines" that opens the rest in place, then the
 * last 5. Output of 25 lines or fewer shows whole. Empty output reads "Empty" (ink3).
 * DET-65: for an output too large to fetch whole, `tail` holds its separately fetched end. The lines between are not loaded, so the divider is
 * not a button.
 */
export function OutputWell({ text, tail }: { text: string; tail: string | null }) {
  const [open, setOpen] = useState(false)
  const view = useMemo(() => outputView(text, tail), [text, tail])
  if (view.head.length === 0 && view.tail.length === 0) {
    return <div className="rounded-card bg-l2 px-3 py-2.5 text-ink3">Empty</div>
  }
  const lines = (ls: readonly string[]) => (ls.length === 0 ? null : <div>{ls.join('\n')}</div>)
  return (
    <div className="max-h-[480px] overflow-auto rounded-card bg-l2 px-3 py-2.5">
      <div className="fig whitespace-pre-wrap text-ink1 [overflow-wrap:anywhere]" data-testid="output-lines">
        {lines(view.head)}
        {open ? lines(view.middle) : null}
      </div>
      {view.partial ? (
        <div className="my-1 text-ink3" aria-label="Lines not loaded">
          …
        </div>
      ) : view.hidden > 0 ? (
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="my-1 flex h-7 w-full items-center gap-1 rounded-chip text-struct hover:bg-l3"
        >
          <Icon name="chevron-right" tone="struct" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
          {open ? `Hide ${view.hidden} lines` : `… ${view.hidden} more lines`}
        </button>
      ) : null}
      <div className="fig whitespace-pre-wrap text-ink1 [overflow-wrap:anywhere]">{lines(view.tail)}</div>
    </div>
  )
}
