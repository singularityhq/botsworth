import { client, urls } from '../../api/client'
import type { Histogram as HistogramWire } from '../../api/types'
import { useApi } from '../../api/useApi'
import { useNav } from '../../state/nav'
import type { Bars, MsRange } from '../../state/url'
import { ErrorCard } from '../../ui/ErrorCard'
import { Skeleton } from '../../ui/Skeleton'
import { Histogram } from '../shared/Histogram'

interface OverTimeProps {
  session: string
  bars: Bars
  tz: string
  /** The brushed session range (SES-30). */
  range: MsRange | null
  /** The open turn's start, drawn as the cursor line (SES-29). */
  cursorMs: number | null
}

/** SES-29, SES-30: the session's Over time histogram. A brush of 4 px or more sets the `range` that filters the turn list. */
export function OverTime({ session, bars, tz, range, cursorMs }: OverTimeProps) {
  const nav = useNav()
  const hist = useApi<HistogramWire>(urls.sessionHistogram(session), (signal) => client.sessionHistogram(session, {}, signal))
  if (hist.error) return <ErrorCard noun="the histogram" message={hist.error.message} onRetry={hist.retry} />
  if (!hist.data) {
    return (
      <div role="status" aria-label="Loading" className="px-6 pb-2 pt-3">
        <Skeleton width={160} height={20} />
        <div className="mt-2">
          <Skeleton height={104} />
        </div>
      </div>
    )
  }
  return (
    <Histogram
      data={hist.data}
      bars={bars}
      tz={tz}
      range={range}
      cursorMs={cursorMs}
      hint="Drag across to show the turns in that range"
      onBrush={(r) => nav.replace({ range: r })}
    />
  )
}
