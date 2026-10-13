import { useEffect, useRef, useState } from 'react'
import { Icon } from '../../ui/Icon'
import type { IconName } from '../../ui/glyphs'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'

/** The small pieces every kind of detail uses: a section with its head (DET-1), the hero, an `info` line, a skeleton well, copy. */

/** DET-1: the head is 20 px high (14/600 ink1, sentence case, SYS-17) with 8 px below; the count after it is ink3. */
export function Section({ icon, title, count, aside, children }: { icon?: IconName; title: string; count?: number | string | null; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <h3 className="flex min-h-5 items-center gap-1.5 text-body font-semibold text-ink1">
        {icon ? <Icon name={icon} tone="quiet" /> : null}
        {title}
        {count !== undefined && count !== null ? <span className="fig font-normal text-ink3">{count}</span> : null}
        {aside ? <span className="ml-1 flex items-center gap-1.5 font-normal text-ink3">{aside}</span> : null}
      </h3>
      {children}
    </section>
  )
}

/** DET-10, DET-20, DET-30, DET-40: the hero figure (30/500 mono) and what stands beside it, 16 px apart. */
export function Hero({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">{children}</div>
}

export function HeroFigure({ children, ink = 'text-ink1', title }: { children: React.ReactNode; ink?: string; title?: string }) {
  return (
    <span title={title} className={`fig text-display font-medium ${ink}`}>
      {children}
    </span>
  )
}

/** SYS-31: one ink3 line with `info`, for a fact that is not in the data. */
export function InfoLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-body text-ink3">
      <Icon name="info" tone="muted" className="mt-0.5" />
      <span className="min-w-0">{children}</span>
    </p>
  )
}

/** DET-60: three skeleton lines, l3 on the l2 well (SYS-54). */
export function WellSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-2.5 rounded-card bg-l2 px-3 py-3">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} aria-hidden="true" className="skeleton block" style={{ height: 14, width: `${i === lines - 1 ? 55 : 100 - i * 8}%`, background: 'var(--color-l3)' }} />
      ))}
    </div>
  )
}

/** DET-70: skeleton rows inside a section while the op detail loads. */
export function RowsSkeleton({ rows = 2 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex h-8 items-center px-2">
          <Skeleton height={14} width={`${50 + i * 18}%`} />
        </div>
      ))}
    </div>
  )
}

const COPIED_MS = 1200

/** DET-23: `copy` turns into `check` for 1.2 s. 28×28. */
export function CopyButton({ text, label, className = '' }: { text: string; label: string; className?: string }) {
  const [done, setDone] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), [])
  const copy = () => {
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(text))
      .then(
      () => {
        setDone(true)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setDone(false), COPIED_MS)
      },
      () => {
        // FALLBACK-OK: DET-23 (a refused clipboard write leaves the button as it was; nothing was copied, so no check is shown)
      },
    )
  }
  return (
    <Tooltip content={done ? 'Copied' : label}>
      <button
        type="button"
        aria-label={done ? 'Copied' : label}
        onClick={copy}
        className={`inline-flex h-7 w-7 items-center justify-center rounded-control text-ink3 transition-colors duration-150 hover:bg-l3 hover:text-struct ${className}`}
      >
        <Icon name={done ? 'check' : 'copy'} tone="inherit" className={done ? 'text-struct' : ''} />
      </button>
    </Tooltip>
  )
}

/** DET-65, DET-66: the `src://` pointer in mono ink3 with a copy button. */
export function PointerLine({ src }: { src: string }) {
  return (
    <div className="flex items-center gap-1.5 text-ink3">
      <span className="fig min-w-0 flex-1 break-all">{src}</span>
      <CopyButton text={src} label="Copy pointer" />
    </div>
  )
}

/** A disclosure toggle: `chevron-right` that turns 90° when open (DET-13, DET-23). */
export function Disclosure({ open, onClick, children }: { open: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-expanded={open} onClick={onClick} className="inline-flex h-7 items-center gap-1 rounded-control text-struct transition-colors duration-150 hover:underline">
      <Icon name="chevron-right" tone="struct" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
      {children}
    </button>
  )
}
