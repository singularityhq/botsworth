import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import type { Day } from '../../api/types'
import { PRESETS, resolvePeriod, type Period } from '../../state/period'
import { Icon } from '../../ui/Icon'
import { Popover } from '../../ui/Popover'
import { commitField, fieldText, presetRange, type Extent } from './explorer.logic'

/** EXP-13 to EXP-16: presets with their resolved ranges, two text fields for a custom range, and a footer naming the browser zone. */
export interface PeriodPopoverProps {
  open: boolean
  onClose: () => void
  anchorRef: RefObject<HTMLElement | null>
  period: Period
  today: Day
  tz: string
  extent: Extent | null
  onApply: (p: Period) => void
}

const ITEM = 'data-pop-item'

function Body({ period, today, tz, extent, onApply }: Omit<PeriodPopoverProps, 'open' | 'onClose' | 'anchorRef'>) {
  const root = useRef<HTMLDivElement>(null)
  const range = resolvePeriod(period, today)
  // All time has no days of its own: the fields then show the data extent.
  const shown = {
    from: range.from ?? extent?.first_day ?? today, // FALLBACK-OK: explorer.md EXP-13 (all time: the fields show the data extent, or today when there is no data)
    to: range.to ?? extent?.last_day ?? today, // FALLBACK-OK: explorer.md EXP-13 (same)
  }
  const [fromText, setFromText] = useState(fieldText(shown.from))
  const [toText, setToText] = useState(fieldText(shown.to))

  useEffect(() => {
    // The popover is hidden until its first measure places it, and a hidden element cannot take focus: wait one frame.
    const id = requestAnimationFrame(() => {
      const active = root.current?.querySelector<HTMLElement>('[aria-current="true"]') ?? root.current?.querySelector<HTMLElement>(`[${ITEM}]`)
      active?.focus()
    })
    return () => cancelAnimationFrame(id)
  }, [])

  const move = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    const items = [...(root.current?.querySelectorAll<HTMLElement>(`[${ITEM}]`) ?? [])]
    const i = items.indexOf(document.activeElement as HTMLElement)
    const next = items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]
    e.preventDefault()
    next?.focus()
  }

  const commit = (field: 'from' | 'to') => {
    const next = commitField(shown, field, field === 'from' ? fromText : toText, today)
    if (next === null) {
      // EXP-15: text that does not parse is not applied; the field shows its previous value again
      if (field === 'from') setFromText(fieldText(shown.from))
      else setToText(fieldText(shown.to))
      return
    }
    onApply({ kind: 'custom', from: next.from, to: next.to })
  }

  const field = (name: 'from' | 'to', label: string, text: string, set: (t: string) => void, prev: Day) => (
    <label className="flex flex-1 flex-col gap-1 text-body text-ink3">
      {label}
      <input
        {...{ [ITEM]: '' }}
        data-focus-quiet
        type="text"
        value={text}
        onChange={(e) => set(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit(name)
          }
        }}
        onBlur={() => set(fieldText(prev))}
        spellCheck={false}
        autoComplete="off"
        className="h-8 w-full rounded-control border border-edge bg-l2 px-2.5 font-mono text-body text-ink1 focus:border-struct-line focus:outline-none"
      />
    </label>
  )

  return (
    <div ref={root} onKeyDown={move} className="flex flex-col">
      <div role="group" aria-label="Presets" className="flex flex-col">
        {PRESETS.map((p) => {
          const active = period.kind === 'preset' && period.preset === p.id
          return (
            <button
              key={p.id}
              type="button"
              {...{ [ITEM]: '' }}
              aria-current={active ? 'true' : undefined}
              onClick={() => onApply({ kind: 'preset', preset: p.id })}
              className="flex h-9 items-center gap-2 rounded-control px-3 text-body text-ink1 transition-colors duration-150 hover:bg-l4"
            >
              <span className="inline-flex w-4 shrink-0">{active ? <Icon name="check" tone="struct" /> : null}</span>
              <span className={`flex-1 ${active ? 'font-medium' : ''}`}>{p.label}</span>
              <span className="font-mono text-ink3">{presetRange({ kind: 'preset', preset: p.id }, today, extent)}</span>
            </button>
          )
        })}
      </div>
      <div className="mt-2 flex gap-2 px-3 pt-2">
        {field('from', 'From', fromText, setFromText, shown.from)}
        {field('to', 'To', toText, setToText, shown.to)}
      </div>
      <p className="m-0 px-3 pt-3 pb-2 text-body text-ink3">Days are in {tz}, your browser's time zone. A session counts in every day it made a model call.</p>
    </div>
  )
}

export function PeriodPopover({ open, onClose, anchorRef, ...rest }: PeriodPopoverProps) {
  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={340} align="start" label="Period">
      <Body {...rest} />
    </Popover>
  )
}
