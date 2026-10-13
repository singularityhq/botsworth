import type { RefObject } from 'react'
import { Icon } from '../../ui/Icon'

/** EXP-10, EXP-11: the title is the period control, a button with its full text as the accessible name. */
export function PeriodTitle({
  text, buttonRef, open, onToggle,
}: { text: string; buttonRef: RefObject<HTMLButtonElement | null>; open: boolean; onToggle: () => void }) {
  return (
    <h1 className="m-0 text-lead font-semibold text-ink1">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={text}
        onClick={onToggle}
        className="inline-flex items-center gap-2 rounded-control text-lead font-semibold text-ink1 transition-colors duration-150 hover:text-struct"
      >
        {text}
        <Icon name="chevron-down" size={20} tone="quiet" />
      </button>
    </h1>
  )
}
