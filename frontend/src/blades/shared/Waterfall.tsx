import { Tooltip } from '../../ui/Tooltip'
import type { Axis } from './histogram.logic'
import { foldMarks, headerLabels, type BarBox } from './waterfall.logic'

/**
 * The Waterfall pattern's parts (SES-40), shared by the session Waterfall and the turn Waterfall (TRN-40). A view lays out its own rows and
 * columns; these draw the time axis head and the bars on the track column.
 */

/** The track column of a row: the bars are placed in px against it, so it must have the width the axis was built for. */
export function Track({ children, className = '', ...rest }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span {...rest} className={`relative block h-full min-w-0 ${className}`}>
      {children}
    </span>
  )
}

interface BarProps {
  box: BarBox
  /** A CSS style for the fill: a token colour or a hatch (SES-20). */
  style: React.CSSProperties
  /** Height in px; the bar is centred vertically. */
  height: number
  title?: string
  outline?: boolean
}

/** One bar of a row on the track: from its start to its end, at least 2 px wide (SES-40). */
export function Bar({ box, style, height, title, outline = false }: BarProps) {
  return (
    <i
      title={title}
      className="absolute top-1/2 block rounded-[2px]"
      style={{ left: box.left, width: box.width, height, marginTop: -height / 2, outline: outline ? '1px solid var(--color-struct-line)' : undefined, ...style }}
    />
  )
}

interface AxisHeadProps {
  axis: Axis | null
  tz: string
}

/**
 * SES-40 / SES-42: the clock-label row, and below it the fold marks. The marks sit only in the head, never in the rows; marks closer than
 * about 10 px merge into "≈×N" with a tooltip. Fills the track cell of the column header.
 */
export function AxisHead({ axis, tz }: AxisHeadProps) {
  if (!axis) return null
  const marks = foldMarks(axis)
  const labels = headerLabels(axis, tz)
  return (
    <>
      {labels.map((l) => (
        <span key={l.t} className="fig absolute top-[22px] -translate-x-1/2 whitespace-nowrap text-label text-ink3" style={{ left: l.x }}>
          {l.text}
        </span>
      ))}
      {marks.map((m) => (
        <span key={m.x0} className="absolute top-10" style={{ left: m.x0, width: m.x1 - m.x0 }}>
          <Tooltip content={m.title}>
            <span className="fig block w-full whitespace-nowrap text-center text-label text-ink2">{m.text}</span>
          </Tooltip>
          <i
            aria-hidden="true"
            className="mt-0.5 block h-1.5 w-full"
            style={{ background: 'repeating-linear-gradient(135deg, var(--color-ink3) 0 1px, transparent 1px 4px)' }}
          />
        </span>
      ))}
    </>
  )
}
