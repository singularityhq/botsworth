import { Icon } from '../../ui/Icon'
import type { IconName } from '../../ui/glyphs'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import type { KindInfo } from '../shared/states'

const KIND_TINT = { message: 'text-type-user', slash: 'text-type-user', resume: 'text-type-user', agent: 'text-type-team', plugin: 'text-type-sys', scripted: 'text-type-sys', harness: '' } as const

function IconButton({ label, tip, icon, onClick }: { label: string; tip: string; icon: 'chevron-left' | 'chevron-right' | 'x'; onClick: () => void }) {
  return (
    <Tooltip content={tip}>
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        className="inline-flex h-7 w-7 items-center justify-center rounded-control text-ink3 transition-colors duration-150 hover:bg-l3 hover:text-struct"
      >
        <Icon name={icon} tone="inherit" />
      </button>
    </Tooltip>
  )
}

export interface DetailHeaderProps {
  /** The turn the row is in: its kind glyph and number (DET-2). */
  turnIx: number
  kind: KindInfo | null
  /** "inside <agent name>" when the row is inside an agent. */
  inside: string | null
  /** The clock: `HH:MM:SS` for a point, `HH:MM–HH:MM` for a gap. Null while unknown. */
  clock: string | null
  /** "R of M" and the stepper; null for the message detail, which is not a row of the list (DET-50). */
  stepper: { r: number; m: number } | null
  /** The 20 px glyph and the name (DET-3). Null while the row is unknown. */
  title: { glyph: IconName; text: string } | null
  /** The line under the title ("from <sender>", DET-50). */
  sub?: string | null
  onTurn: () => void
  onPrev: () => void
  onNext: () => void
  onClose: () => void
}

/** DET-2, DET-3: the meta line and the title. Rendered at once from the row the turn blade holds (DET-70). */
export function DetailHeader(p: DetailHeaderProps) {
  return (
    <header className="shrink-0 px-6 pb-4 pt-6">
      <div className="flex min-h-7 items-center gap-2 whitespace-nowrap text-ink3">
        {p.kind ? <Icon name={p.kind.icon} tone="inherit" label={p.kind.label} className={KIND_TINT[p.kind.key]} /> : null}
        <button type="button" onClick={p.onTurn} className="rounded-chip text-ink3 transition-colors duration-150 hover:text-struct focus-visible:text-struct">
          Turn {p.turnIx}
        </button>
        {p.inside ? <span className="truncate">inside {p.inside}</span> : null}
        {p.clock ? <span className="fig">{p.clock}</span> : null}
        <span className="flex-1" />
        {p.stepper ? (
          <>
            {p.stepper.r > 0 ? (
              <span className="fig" aria-label="Position in the turn">
                {p.stepper.r} of {p.stepper.m}
              </span>
            ) : null}
            <IconButton label="Previous" tip="Previous (k)" icon="chevron-left" onClick={p.onPrev} />
            <IconButton label="Next" tip="Next (j)" icon="chevron-right" onClick={p.onNext} />
          </>
        ) : null}
        <IconButton label="Close" tip="Close (Esc)" icon="x" onClick={p.onClose} />
      </div>
      <h2 className="mt-1.5 flex min-w-0 items-start gap-1.5 text-lead font-semibold text-ink1">
        {p.title ? (
          <>
            <Icon name={p.title.glyph} size={20} tone="quiet" className="mt-[3px]" />
            <span title={p.title.text} className="line-clamp-2 min-w-0 break-words">
              {p.title.text}
            </span>
          </>
        ) : (
          <Skeleton width={320} height={22} />
        )}
      </h2>
      {p.sub ? <p className="mt-0.5 text-ink3">{p.sub}</p> : null}
    </header>
  )
}
