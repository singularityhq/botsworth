import { useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { BLADE_MS, GAP, bladeWidths, easeBlade, scrollToNewest, scrollToReveal } from './strip.logic'

export interface BladeSpec {
  key: string
  label: string
  render: (width: number, compact: boolean) => React.ReactNode
}

const PAD = 12
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * SYS-45 to SYS-48, SYS-58: the horizontal strip of blades. One blade fills the stage. Two or more: parents are 400 px, the newest takes the
 * rest (at least 640), and the strip scrolls so the newest is fully in view; older blades stay mounted off-screen to the left.
 * Every change (open, close, crumb, Esc, Back) uses one transition: staying blades resize and the strip scrolls together over 280 ms;
 * a closed blade stays mounted until the transition ends. Under reduced motion all of it is instant.
 */
export function BladeStrip({ blades, focusKey, onFocus }: { blades: readonly BladeSpec[]; focusKey: string; onFocus: (key: string) => void }) {
  const scroller = useRef<HTMLDivElement>(null)
  const frames = useRef(new Map<string, HTMLElement>())
  const shown = useRef(new Map<string, number>()) // width each mounted blade has on screen right now
  const committed = useRef<readonly BladeSpec[]>(blades)
  const raf = useRef(0)
  const moving = useRef(false) // a blade transition is running; it already scrolls to the newest blade
  const seen = useRef<{ keys: string; stageW: number; focusKey: string } | null>(null)
  const [stageW, setStageW] = useState(0)

  const keys = blades.map((b) => b.key).join('|')
  const [prevKeys, setPrevKeys] = useState(keys)
  const [leaving, setLeaving] = useState<readonly BladeSpec[]>([])
  if (keys !== prevKeys) {
    // Derive the leaving blades in the same render that drops them, so their content never unmounts mid-transition.
    setPrevKeys(keys)
    setLeaving(reducedMotion() ? [] : committed.current.filter((p) => !blades.some((b) => b.key === p.key)))
  }

  useLayoutEffect(() => {
    const el = scroller.current!
    const measure = () => setStageW(el.clientWidth - 2 * PAD)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // A slide still running when the strip unmounts would write widths to frames that are gone.
  useLayoutEffect(() => () => cancelAnimationFrame(raf.current), [])

  // The effect below reads the blades through this ref, so a new array each parent render does not restart a transition.
  const latest = useRef(blades)
  useLayoutEffect(() => {
    latest.current = blades
    committed.current = blades
  })

  useLayoutEffect(() => {
    const blades = latest.current
    const el = scroller.current
    if (!el || stageW <= 0) return
    const prev = seen.current
    seen.current = { keys, stageW, focusKey }
    const targets = bladeWidths(blades.length, stageW)
    const setWidths = (ws: number[]) =>
      blades.forEach((b, i) => {
        frames.current.get(b.key)!.style.width = `${ws[i]}px`
        shown.current.set(b.key, ws[i])
      })
    const end = scrollToNewest(targets, stageW)

    if (!prev || prev.stageW !== stageW) {
      cancelAnimationFrame(raf.current)
      moving.current = false
      setLeaving([])
      setWidths(targets)
      el.scrollLeft = end
      return
    }
    if (prev.keys !== keys) {
      cancelAnimationFrame(raf.current)
      moving.current = false
      // FALLBACK-OK: SYS-58 (a blade that was not on screen before enters at its full width and the strip slides to it)
      const from = blades.map((b, i) => shown.current.get(b.key) ?? targets[i])
      if (reducedMotion()) {
        for (const k of [...shown.current.keys()]) if (!blades.some((b) => b.key === k)) shown.current.delete(k)
        setWidths(targets)
        el.scrollLeft = end
        return
      }
      setWidths(from)
      const s0 = el.scrollLeft
      const t0 = performance.now()
      moving.current = true
      const step = (t: number) => {
        const k = Math.min(1, (t - t0) / BLADE_MS)
        const e = easeBlade(k)
        setWidths(from.map((f, i) => f + (targets[i] - f) * e))
        el.scrollLeft = s0 + (end - s0) * e
        if (k < 1) raf.current = requestAnimationFrame(step)
        else {
          moving.current = false
          flushSync(() => setLeaving([]))
          for (const k of [...shown.current.keys()]) if (!blades.some((b) => b.key === k)) shown.current.delete(k)
          setWidths(targets)
          el.scrollLeft = end
        }
      }
      raf.current = requestAnimationFrame(step)
      return
    }
    if (prev.focusKey !== focusKey && !moving.current) {
      const i = blades.findIndex((b) => b.key === focusKey)
      if (i < 0) return
      const to = scrollToReveal(targets, i, el.scrollLeft, stageW)
      if (to === el.scrollLeft) return
      cancelAnimationFrame(raf.current)
      if (reducedMotion()) {
        el.scrollLeft = to
        return
      }
      const s0 = el.scrollLeft
      const t0 = performance.now()
      const step = (t: number) => {
        const k = Math.min(1, (t - t0) / BLADE_MS)
        el.scrollLeft = s0 + (to - s0) * easeBlade(k)
        if (k < 1) raf.current = requestAnimationFrame(step)
      }
      raf.current = requestAnimationFrame(step)
    }
  }, [keys, stageW, focusKey])

  const targets = bladeWidths(blades.length, stageW)
  const leavingWidth = (key: string): number => {
    const w = shown.current.get(key)
    if (w === undefined) throw new Error(`BladeStrip: leaving blade ${key} was never shown`)
    return w
  }
  const frame = (b: BladeSpec, width: number, compact: boolean, ghost: boolean) => {
    const focused = !ghost && b.key === focusKey
    return (
      <section
        key={b.key}
        ref={(n) => {
          if (n) frames.current.set(b.key, n)
          else frames.current.delete(b.key)
        }}
        aria-label={b.label}
        aria-hidden={ghost || undefined}
        data-blade={b.key}
        data-focused={focused}
        onPointerDownCapture={ghost ? undefined : () => onFocus(b.key)}
        className={`relative h-full shrink-0 overflow-hidden rounded-pane border bg-l1 transition-[border-color,box-shadow] duration-200 ease-[var(--ease-standard)] ${
          focused ? 'border-struct-line shadow-pane-focus' : 'border-hair shadow-pane'
        } ${ghost ? 'pointer-events-none' : ''}`}
        style={{ width }}
      >
        {b.render(width, compact)}
      </section>
    )
  }

  return (
    <div ref={scroller} className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden" data-testid="blade-strip">
      <div className="flex h-full w-max p-3" style={{ gap: GAP }}>
        {stageW > 0 ? blades.map((b, i) => frame(b, targets[i], blades.length > 1 && i < blades.length - 1, false)) : null}
        {leaving.map((b) => frame(b, leavingWidth(b.key), false, true))}
      </div>
    </div>
  )
}
