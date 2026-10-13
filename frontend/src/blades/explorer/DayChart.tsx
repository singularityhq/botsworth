import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Day, SessionRow } from '../../api/types'
import type { Measure } from '../../state/url'
import { Icon } from '../../ui/Icon'
import { placeBox, type Placement } from '../../ui/place'
import { seriesVar } from '../../ui/series'
import { Skeleton } from '../../ui/Skeleton'
import {
  LABEL_H, PLOT_H, barHeight, chartKey, columnLabel, columnName, columnsRange, footprint, footprintBox, isClick, measureText, peakIndex,
  slotOrder, tooltipModel, windowMax, type Column, type KeyState,
} from './dayChart.logic'

/** EXP-30 to EXP-41: the measure toggle, the stacked day columns, the max rule, the tooltip, click, drag and keyboard selection. */

/** EXP-31: `circle-dollar-sign` Cost and `coins` Tokens, 32 px; the selected segment is l3 with ink1 500 text, the other ink3. */
export function MeasureToggle({ value, onChange }: { value: Measure; onChange: (m: Measure) => void }) {
  const opts = [
    { value: 'cost', label: 'Cost', icon: 'circle-dollar-sign' },
    { value: 'tokens', label: 'Tokens', icon: 'coins' },
  ] as const
  return (
    <div role="group" aria-label="Measure" className="inline-flex h-8 overflow-hidden rounded-control border border-edge">
      {opts.map((o, i) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={`inline-flex items-center gap-2 px-3 text-body transition-colors duration-150 ease-[var(--ease-standard)] ${i > 0 ? 'border-l border-edge' : ''} ${
              on ? 'bg-l3 font-medium text-ink1' : 'text-ink3 hover:text-struct'
            }`}
          >
            <Icon name={o.icon} tone="inherit" />
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

function Tip({ col, measure, anchor }: { col: Column; measure: Measure; anchor: DOMRect }) {
  const box = useRef<HTMLDivElement>(null)
  const [at, setAt] = useState<Placement | null>(null)
  const tip = tooltipModel(col, measure)
  useLayoutEffect(() => {
    if (!box.current) return
    const b = box.current.getBoundingClientRect()
    setAt(placeBox(anchor, { w: b.width, h: b.height }, { w: window.innerWidth, h: window.innerHeight }, { side: 'top', align: 'center', gap: 6 }))
  }, [anchor, col, measure])
  return createPortal(
    <div
      ref={box}
      role="tooltip"
      className="pointer-events-none fixed z-[80] min-w-[200px] rounded-control bg-l4 px-2.5 py-2 text-body text-ink1 shadow-pop"
      // FALLBACK-OK: SYS-43 (the tooltip is hidden until its first measure places it)
      style={{ left: at?.left ?? 0, top: at?.top ?? 0, visibility: at ? 'visible' : 'hidden', fontVariantNumeric: 'tabular-nums' }}
    >
      <div className="mb-1 flex justify-between gap-3">
        <span>{tip.title}</span>
        <b className={`fig font-semibold ${measure === 'cost' ? 'text-money' : 'text-ink1'}`}>{tip.total}</b>
      </div>
      {tip.lines.map((l) => (
        <div key={l.name} className="flex items-center justify-between gap-3 text-ink2">
          <span className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" className="inline-block size-2 rounded-full" style={{ background: seriesVar(l.slot) }} />
            {l.name}
          </span>
          <span className="fig">{l.value}</span>
        </div>
      ))}
      {tip.more > 0 ? <div className="text-ink3">{tip.more} more</div> : null}
    </div>,
    document.body,
  )
}

export interface DayChartProps {
  columns: readonly Column[]
  measure: Measure
  /** The session under the pointer or the list cursor (EXP-40). */
  hoverSession: SessionRow | null
  /** The legend project under the pointer, as its slot (EXP-41); undefined for none. null is the "other" slot. */
  hoverSlot: number | undefined
  /** Draft range while a drag runs: the band follows the pointer (EXP-38). */
  draft: { from: Day; to: Day } | null
  onDraft: (r: { from: Day; to: Day } | null) => void
  onSelect: (r: { from: Day; to: Day }) => void
}

/** The plot, labels and interactions. The caller wraps it with the header row. */
export function DayChart({ columns, measure, hoverSession, hoverSlot, draft, onDraft, onSelect }: DayChartProps) {
  const max = windowMax(columns)
  const peak = peakIndex(columns)
  const foot = useMemo(() => (hoverSession ? footprint(hoverSession, columns, measure) : null), [hoverSession, columns, measure])
  const drag = useRef<{ anchor: number; current: number } | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const [tip, setTip] = useState<{ i: number; rect: DOMRect } | null>(null)
  const [keys, setKeys] = useState<KeyState>({ focus: Math.max(0, columns.findIndex((c) => c.inPeriod)), anchor: null })

  const inDraft = (c: Column) => (draft ? diff(draft.from, c.to) && diff(c.from, draft.to) : c.inPeriod)
  const colAt = (x: number, y: number): number | null => {
    const el = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-col]')
    return el && root.current?.contains(el) ? Number(el.dataset.col) : null
  }

  useEffect(() => {
    const up = () => {
      const d = drag.current
      if (!d) return
      drag.current = null
      onDraft(null)
      onSelect(columnsRange(columns, d.anchor, isClick(d.anchor, d.current) ? d.anchor : d.current))
    }
    const move = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      const i = colAt(e.clientX, e.clientY)
      if (i === null || i === d.current) return
      d.current = i
      onDraft(columnsRange(columns, d.anchor, i))
    }
    window.addEventListener('pointerup', up)
    window.addEventListener('pointermove', move)
    return () => {
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointermove', move)
    }
    // colAt reads only refs and the DOM
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, onDraft, onSelect])

  // The hover state must not outlive its column set: a click can shrink the window under a stored index.
  const tipCol = tip ? (columns[tip.i] as Column | undefined) : undefined
  const focusCol = Math.min(keys.focus, columns.length - 1)

  const showTip = (i: number, el: HTMLElement) => setTip({ i, rect: el.getBoundingClientRect() })

  return (
    <div
      ref={root}
      role="group"
      aria-label="Days"
      className="relative select-none touch-none"
      onPointerLeave={() => setTip(null)}
      onKeyDown={(e) => {
        const r = chartKey(keys, e.key, e.shiftKey, columns)
        if (!r) return
        e.preventDefault()
        e.stopPropagation() // the shell would also read the arrows as a blade-focus move (SYS-50)
        setKeys(r.state)
        if (r.range) onSelect(r.range)
        const el = root.current?.querySelector<HTMLElement>(`[data-col="${r.state.focus}"]`)
        el?.focus()
      }}
    >
      {max > 0 ? (
        <div aria-hidden="true" className="pointer-events-none absolute right-0 left-0 z-[1] border-t border-dashed border-rule" style={{ top: LABEL_H }}>
          <span className={`absolute left-0 font-mono text-label ${measure === 'cost' ? 'text-money' : 'text-ink2'}`} style={{ top: -LABEL_H }}>
            {measureText(max, measure)}
          </span>
        </div>
      ) : null}
      <div className="flex gap-1">
        {columns.map((c, i) => {
          const sel = inDraft(c)
          const h = barHeight(c.total, max)
          const box = foot?.get(i) && hoverSession ? footprintBox(c, hoverSession.color_slot, foot.get(i)!.value, max) : null
          return (
            <div
              key={c.from}
              data-col={i}
              role="button"
              tabIndex={i === focusCol ? 0 : -1}
              aria-label={columnName(c, measure)}
              aria-pressed={sel}
              onFocus={(e) => {
                setKeys((k) => (k.focus === i ? k : { focus: i, anchor: k.anchor }))
                showTip(i, e.currentTarget)
              }}
              onBlur={() => setTip(null)}
              onPointerEnter={(e) => showTip(i, e.currentTarget)}
              onPointerDown={(e) => {
                if (e.button !== 0) return
                e.preventDefault()
                drag.current = { anchor: i, current: i }
                setKeys({ focus: i, anchor: null })
              }}
              className={`relative flex min-w-0 flex-1 cursor-pointer flex-col rounded-[6px] px-0.5 ${c.monday ? 'ml-2' : ''} ${sel ? 'bg-l2' : 'hover:bg-hair'}`}
            >
              {foot?.has(i) ? <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[2] rounded-[6px] border-[1.5px] border-struct" /> : null}
              <div className="relative flex items-end justify-center" style={{ height: PLOT_H + LABEL_H }}>
                {i === peak ? (
                  <span
                    className="absolute z-[3] font-mono text-label whitespace-nowrap text-ink2"
                    style={{ bottom: h + 2, left: i === 0 ? 0 : '50%', transform: i === 0 ? undefined : 'translateX(-50%)' }}
                  >
                    {measureText(c.total, measure, c.lowerBound)}
                  </span>
                ) : null}
                <div className="flex w-[72%] flex-col-reverse overflow-hidden rounded-t-[3px] rounded-b-[1px]" style={{ height: h, opacity: sel ? 1 : 0.55 }}>
                  {c.segments.map((s) => (
                    <i
                      key={slotOrder(s.slot)}
                      className="block flex-none border-t border-l1"
                      style={{
                        height: (s.value / max) * PLOT_H,
                        background: seriesVar(s.slot),
                        opacity: hoverSlot !== undefined && hoverSlot !== slotOrder(s.slot) ? 0.28 : 1,
                      }}
                    />
                  ))}
                </div>
                {box ? (
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute right-[14%] left-[14%] border border-ink1"
                    style={{ bottom: box.bottom, height: box.height }}
                  />
                ) : null}
              </div>
              <div className={`py-1 text-center text-body tabular-nums ${sel ? 'font-medium text-ink1' : 'text-ink3'}`}>
                {columnLabel(c, i > 0 ? columns[i - 1] : null)}
              </div>
            </div>
          )
        })}
      </div>
      {tipCol ? <Tip col={tipCol} measure={measure} anchor={tip!.rect} /> : null}
    </div>
  )
}

const diff = (a: Day, b: Day) => a <= b // 'YYYY-MM-DD' strings order like dates

/** SYS-54: the first load shows column skeletons, never zeros. */
export function DayChartSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="flex gap-1">
      {Array.from({ length: 21 }, (_, i) => (
        <div key={i} className="flex min-w-0 flex-1 flex-col justify-end px-0.5" style={{ height: PLOT_H + LABEL_H + 28 }}>
          <Skeleton height={30 + ((i * 53) % 150)} />
        </div>
      ))}
    </div>
  )
}
