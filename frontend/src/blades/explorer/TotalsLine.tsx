import { fmt } from '../../lib/format'
import { Money } from '../../ui/figures'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import { LOST_MIN_MS } from '../shared/causes'
import type { TotalsView } from './explorer.logic'

/** D51: the cost and tokens are whole-session figures, so they differ from the day bar, which counts calls that started that day. */
export const WHOLE_SESSIONS = 'Whole sessions active in this period'

/** EXP-12: `$2,570.00   8.69B tokens   212 sessions   12 automated hidden`. Figures are 20/600; unit words are 14/400 ink3; 16 px between items. */
export function TotalsLine({ view, review }: { view: TotalsView; review?: { lostMs: number; onOpen: () => void } }) {
  return (
    <p className="m-0 flex flex-wrap items-baseline gap-x-4 gap-y-1">
      <Money value={view.cost} lowerBound={view.costLowerBound} nUnpriced={view.nUnpriced} weight={600} className="text-lead" />
      <span>
        <span className="fig text-lead font-semibold text-ink1">{fmt.tokens(view.tokens)}</span> <span className="text-body text-ink3">tokens</span>
      </span>
      <span>
        <span className="fig text-lead font-semibold text-ink1">{view.sessions}</span>
        {view.of !== null ? <span className="text-body text-ink3"> of {view.of}</span> : null}{' '}
        <span className="text-body text-ink3">{view.of === null && view.sessions === 1 ? 'session' : 'sessions'}</span>
      </span>
      {view.hidden > 0 ? <span className="text-body text-ink3">{view.hidden} automated hidden</span> : null}
      {review ? <ReviewEntry {...review} /> : null}
      <span className="basis-full text-body text-ink3">{WHOLE_SESSIONS}</span>
    </p>
  )
}

/** REV-21: the last item of the totals line opens the scope review: "<lost> lost" from 60 s, else "Review". */
function ReviewEntry({ lostMs, onOpen }: { lostMs: number; onOpen: () => void }) {
  return (
    <Tooltip content="Where this scope lost time (r)">
      <button type="button" onClick={onOpen} className="hit-24 rounded-chip text-body text-ink2 hover:underline">
        {lostMs >= LOST_MIN_MS ? (
          <>
            <b className="fig font-medium text-error">{fmt.dur(lostMs)}</b> lost
          </>
        ) : (
          'Review'
        )}
      </button>
    </Tooltip>
  )
}

/** The status says there is no store to ask (EXP-82), or a request is still out (SYS-54). */
export function TotalsNoStore() {
  return (
    <p className="m-0 flex items-baseline gap-1">
      <span className="fig text-lead font-semibold text-ink1">0</span> <span className="text-body text-ink3">sessions</span>
    </p>
  )
}

export function TotalsSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="flex items-center gap-4" style={{ height: 26 }}>
      <Skeleton width={96} height={20} />
      <Skeleton width={104} height={20} />
      <Skeleton width={96} height={20} />
    </div>
  )
}
