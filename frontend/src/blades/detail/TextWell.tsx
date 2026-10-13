import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '../../ui/Icon'
import { clampOf, imageLine, splitImages } from './detail.logic'
import { CopyButton } from './parts'

/** The 20 px line of the body text (`--text-body--line-height`): the unit the clamp is measured in. */
const LINE_PX = 20

export interface TextWellProps {
  text: string
  /** `prose` is capped at 76 ch (SYS-19); `code` is mono and as wide as the blade. */
  variant: 'prose' | 'code'
  /** Lines shown before "Show all": 3 for thinking and asks, 6 for said text and commands. */
  clamp?: number
  /** The expander's wording: "Show all · N lines" (DET-13) or "All N lines" (DET-23). */
  expander?: (lines: number) => string
  copy?: boolean
  label: string
}

/**
 * DET-13, DET-23, DET-32: an l2 well holding text. It clamps to `clamp` lines with an expander in struct, and has a copy button at the top
 * right (28×28). An inline image in the text is never drawn: it is one ink3 `info` line "Image not shown (size)" (DET-67).
 * The disclosure resets with the row because the detail blade remounts its body per row (DET-4).
 */
export function TextWell({ text, variant, clamp, expander = (n) => `Show all · ${n} lines`, copy = true, label }: TextWellProps) {
  const [open, setOpen] = useState(false)
  const [visual, setVisual] = useState<number | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const pieces = useMemo(() => splitImages(text), [text])
  const shown = useMemo(() => pieces.flatMap((p) => (p.kind === 'text' ? [p.text] : [])).join(''), [pieces])

  // The wrapped line count is measured from the full content height; it changes when the blade changes width.
  useLayoutEffect(() => {
    const el = box.current
    if (!el || clamp === undefined) return
    const measure = () => setVisual(Math.round(el.scrollHeight / LINE_PX))
    measure()
    // FALLBACK-OK: DET-13 (an environment without ResizeObserver measures once; the browser always has it)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [text, clamp, open])

  const info = clamp === undefined ? null : clampOf(shown, clamp, visual)
  const collapsed = info !== null && info.clamped && !open
  const style: React.CSSProperties | undefined = collapsed
    ? { display: '-webkit-box', WebkitLineClamp: clamp, WebkitBoxOrient: 'vertical', overflow: 'hidden' }
    : undefined

  return (
    <div className={`relative rounded-card bg-l2 px-3 py-2.5 ${variant === 'prose' ? 'max-w-[76ch]' : ''}`}>
      {copy ? (
        <span className="absolute right-1 top-1">
          <CopyButton text={shown} label={`Copy ${label}`} />
        </span>
      ) : null}
      <div ref={box} aria-label={label} style={style} className={`text-ink1 [overflow-wrap:anywhere] whitespace-pre-wrap ${variant === 'code' ? 'fig' : ''} ${copy ? 'pr-8' : ''}`}>
        {pieces.map((p, i) =>
          p.kind === 'text' ? (
            <span key={i}>{p.text}</span>
          ) : (
            <span key={i} className="my-1 flex items-center gap-1.5 font-sans text-ink3">
              <Icon name="info" tone="muted" />
              {imageLine(p.bytes)}
            </span>
          ),
        )}
      </div>
      {info !== null && info.clamped ? (
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="mt-1.5 inline-flex h-6 items-center gap-1 rounded-chip font-sans text-struct hover:underline"
        >
          <Icon name="chevron-right" tone="struct" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
          {open ? 'Show less' : expander(info.lines)}
        </button>
      ) : null}
    </div>
  )
}
