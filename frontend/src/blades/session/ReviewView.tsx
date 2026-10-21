import type { Evidence, Finding, ReviewReport } from '../../api/types'
import { fmt } from '../../lib/format'
import { Duration } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import { ErrorCard } from '../../ui/ErrorCard'
import { segmentWidths } from '../shared/answers.logic'
import { CAUSES, causeFill } from '../shared/causes'
import { useElementWidth } from '../shared/useElementWidth'
import { bars, CHANGE, claim, evidenceText, facts, footnote, heading, legend, outsideATurn, sharedNames, startText, type Bar } from './review.logic'

/** REV-4 to REV-8: the Review view of a session. */

const BAR_GAP_PX = 2

/** Left edge and width of each segment, in px when the bar is measured, else in % of the bar (a close first paint). */
function segmentBox(bar: Bar, widths: number[] | null): { left: string | number; width: string | number }[] {
  let cum = 0
  let x = 0
  return bar.segments.map((s, i) => {
    const box = widths ? { left: x, width: widths[i] } : { left: `${(cum / bar.totalMs) * 100}%`, width: `${(s.ms / bar.totalMs) * 100}%` }
    cum += s.ms
    if (widths) x += widths[i] + BAR_GAP_PX
    return box
  })
}

/** REV-5: one 16 px bar. Its width is its share of the wall (the full width is the wall); segments are at least 2 px.
 *  The labels of segments of 6 % or more sit in a line above the bar, at the segment's left edge and cut to its width (REV-1). */
function CauseBar({ bar, fullPx }: { bar: Bar; fullPx: number }) {
  const widths = fullPx > 0 ? segmentWidths(bar.segments.map((s) => s.ms), fullPx * bar.width, 2, BAR_GAP_PX) : null
  const boxes = segmentBox(bar, widths)
  return (
    <div data-bar={bar.caption}>
      <p className="mb-1 text-body">
        <span className="text-ink2">{bar.caption}</span> <Duration ms={bar.totalMs} ink="ink1" className="font-medium" />
      </p>
      <div data-labels className="relative h-4 text-label text-ink3" style={{ width: `${bar.width * 100}%` }}>
        {bar.segments.map((s, i) =>
          s.labelled ? (
            <span key={s.cause} data-label={s.cause} className="absolute top-0 truncate whitespace-nowrap" style={boxes[i]}>
              {CAUSES[s.cause].short}
            </span>
          ) : null,
        )}
      </div>
      <div role="img" aria-label={`${bar.caption} ${fmt.dur(bar.totalMs)}`} className="flex h-4 gap-0.5 overflow-hidden rounded-[4px]" style={{ width: `${bar.width * 100}%` }}>
        {bar.segments.map((s, i) => (
          <Tooltip key={s.cause} content={s.tooltip}>
            <i data-cause={s.cause} data-ms={s.ms} className="block h-4" style={{ ...causeFill(s.cause), width: widths ? widths[i] : undefined, minWidth: 2 }} />
          </Tooltip>
        ))}
      </div>
    </div>
  )
}

function Legend({ causes }: { causes: ReviewReport['causes'] }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1" aria-label="Causes">
      {legend(causes).map((c) => (
        <Tooltip key={c.cause} content={`${CAUSES[c.cause].label}: ${CAUSES[c.cause].note}`}>
          <span className="inline-flex items-center gap-1.5 text-ink3">
            <i className="block h-2.5 w-2.5 shrink-0 rounded-[2px]" style={causeFill(c.cause)} />
            {CAUSES[c.cause].short}
            <Duration ms={c.ms} ink="ink1" className="font-medium" />
          </span>
        </Tooltip>
      ))}
    </div>
  )
}

interface CardProps {
  finding: Finding
  review: ReviewReport
  scope: boolean
  tz: string
  onOpenSession: ((session: string) => void) | undefined
  onOpenEvidence: ((e: Evidence) => void) | undefined
}

/** REV-6: one finding. */
function Card({ finding: f, review, scope, tz, onOpenSession, onOpenEvidence }: CardProps) {
  const evidence = evidenceText(f, tz, scope)
  const change = CHANGE[f.cause]
  const factsText = facts(f, review)
  const shared = sharedNames(f.sessions)
  return (
    <article data-finding={f.cause} className={`grid gap-x-4 gap-y-1 rounded-card bg-l2 p-4 grid-cols-[120px_minmax(0,1fr)]`}>
      <div>
        <div className={`fig text-lead font-medium ${f.lost ? 'text-error' : 'text-ink1'}`}>{fmt.dur(f.ms)}</div>
        {f.lost ? <div className="text-label text-error">lost</div> : null}
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-body font-semibold text-ink1">{heading(f, scope)}</h3>
        {factsText !== '' ? <p className="text-body text-ink2">{factsText}</p> : null}
        {evidence !== null && outsideATurn(f) ? (
          <p data-evidence="text" className="text-body text-ink2">
            {evidence}
          </p>
        ) : evidence !== null ? (
          <button
            type="button"
            disabled={!onOpenEvidence}
            onClick={() => onOpenEvidence?.(f.longest!)}
            className="self-start text-left text-body text-struct hover:underline disabled:cursor-default disabled:no-underline"
          >
            {evidence}
          </button>
        ) : null}
        {scope && f.sessions.length > 0 ? (
          <p className="text-body text-ink3">
            Most in:{' '}
            {f.sessions.map((x, i) => (
              <span key={x.session}>
                {i > 0 ? ' · ' : ''}
                <button type="button" data-session-link={x.session} onClick={() => onOpenSession?.(x.session)} className="text-body text-struct hover:underline">
                  {x.name}
                  {shared.has(x.name) ? (
                    <>
                      {' '}
                      <span className="text-ink3">{startText(x.start_ms, tz)}</span>
                    </>
                  ) : null}
                </button>{' '}
                <Duration ms={x.ms} ink="ink2" />
              </span>
            ))}
          </p>
        ) : null}
        {change !== null ? (
          <div className="mt-1 flex gap-2 border-l-2 border-struct-soft pl-2">
            <span className="shrink-0 text-label text-ink3">Change</span>
            <span className="text-body text-ink1">{change}</span>
          </div>
        ) : null}
      </div>
    </article>
  )
}

function ReviewSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4">
      <Skeleton width="70%" height={26} />
      <Skeleton width="100%" height={16} />
      <Skeleton width="80%" height={16} />
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-card bg-l2 p-4">
          <Skeleton width="60%" height={20} />
        </div>
      ))}
    </div>
  )
}

interface ViewProps {
  review: ReviewReport | undefined
  /** REV-20: the scope variant: its claim, evidence line, "Most in:" links and footnote. */
  scope?: boolean
  /** REV-22: a "Most in:" link opens that session in Review. */
  onOpenSession?: (session: string) => void
  error: { message: string } | null
  onRetry: () => void
  tz: string
  /** Opens the evidence row like a finder jump (REV-6); undefined until the turn list has loaded. */
  onOpenEvidence: ((e: Evidence) => void) | undefined
}

export function ReviewView({ review, scope = false, error, onRetry, tz, onOpenSession, onOpenEvidence }: ViewProps) {
  const [fullRef, fullPx] = useElementWidth<HTMLDivElement>()
  let body: React.ReactNode
  if (error) {
    body = <ErrorCard noun="the review" message={error.message} onRetry={onRetry} />
  } else if (!review) {
    body = <ReviewSkeleton />
  } else if (scope && review.n_sessions === 0) {
    body = <p className="text-body text-ink2">No sessions in this scope.</p> // REV-23
  } else {
    const sentences = claim(review, scope)
    body = (
      <>
        <h2 className="text-lead font-semibold text-ink1">{sentences.join(' ')}</h2>
        <div className="flex flex-col gap-3">
          {bars(review).map((b) => (
            <CauseBar key={b.caption} bar={b} fullPx={fullPx} />
          ))}
          <Legend causes={review.causes} />
        </div>
        {review.findings.length === 0 ? (
          <div className="flex items-center gap-2">
            <Icon name="check" size={24} tone="muted" />
            <p className="text-body text-ink2">No cause other than work took 2 min or more.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {review.findings.map((f) => (
              <Card key={f.cause} finding={f} review={review} scope={scope} tz={tz} onOpenSession={onOpenSession} onOpenEvidence={onOpenEvidence} />
            ))}
          </div>
        )}
        <div className="text-label text-ink3">
          {footnote(review, scope).map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
      </>
    )
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4" aria-label="Review">
      <div ref={fullRef} className="flex max-w-[760px] flex-col gap-4">
        {body}
      </div>
    </div>
  )
}
