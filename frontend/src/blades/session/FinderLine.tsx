import type { Bars } from '../../state/url'
import { Icon } from '../../ui/Icon'
import { KeyCap } from '../../ui/KeyCap'
import { Segmented } from '../../ui/Segmented'

const MEASURES = [
  { value: 'cost', label: 'Cost' },
  { value: 'time', label: 'Time' },
  { value: 'tokens', label: 'Tokens' },
] as const satisfies readonly { value: Bars; label: string }[]

interface FinderLineProps {
  find: string
  bars: Bars
  fieldRef: React.RefObject<HTMLInputElement | null>
  onFind: (text: string) => void
  onBars: (bars: Bars) => void
  /** Enter or the down arrow in the field (SES-32): blur it and move the cursor to the first kept turn. */
  onEnter: () => void
  /** F3b: the Ledger | Waterfall segment and its axis segment (SES-41). */
  viewToggle: React.ReactNode
  /** F3b: the Biggest chip and the finder chips (SES-31). */
  finderChips: React.ReactNode
}

/** SES-31: one line under the header: view toggle, search field, measure segment, chips. 8 px gaps. */
export function FinderLine({ find, bars, fieldRef, onFind, onBars, onEnter, viewToggle, finderChips }: FinderLineProps) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-y border-hair px-6 py-2">
      {viewToggle}
      <label className="flex h-8 w-[300px] items-center gap-2 rounded-control border border-edge pl-2.5 pr-1.5 text-ink3 focus-within:border-struct">
        <Icon name="search" />
        <input
          ref={fieldRef}
          data-focus-quiet
          value={find}
          onChange={(e) => onFind(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === 'ArrowDown') {
              e.preventDefault()
              e.currentTarget.blur()
              onEnter()
            }
          }}
          placeholder="Find a step: command, file, tool, text"
          aria-label="Find a step"
          autoComplete="off"
          spellCheck={false}
          className="h-6 min-w-0 flex-1 bg-transparent text-ink1 outline-none placeholder:text-ink3"
        />
        <KeyCap>/</KeyCap>
      </label>
      <span title="Bars and Biggest by cost, time or tokens (1, 2, 3)">
        <Segmented options={MEASURES} value={bars} onChange={onBars} label="Bars by" />
      </span>
      {finderChips}
    </div>
  )
}
