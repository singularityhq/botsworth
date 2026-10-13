import { client, urls } from '../../api/client'
import type { Histogram as HistogramWire } from '../../api/types'
import { useApi } from '../../api/useApi'
import type { Bars, MsRange } from '../../state/url'
import { ErrorCard } from '../../ui/ErrorCard'
import { Skeleton } from '../../ui/Skeleton'
import { Histogram } from '../shared/Histogram'

interface TurnHistogramProps {
  turn: string
  bars: Bars
  tz: string
  zoom: MsRange | null
  /** The cursor row's start: the cursor line (SES-29). */
  cursorMs: number | null
  onBrush: (range: MsRange) => void
}

/**
 * TRN-21 to TRN-24: the turn's histogram. Stacked by model family, over the turn axis with its tail zone, real time while a zoom is set.
 * A brush of 4 px or more sets the zoom.
 */
export function TurnHistogram({ turn, bars, tz, zoom, cursorMs, onBrush }: TurnHistogramProps) {
  const hist = useApi<HistogramWire>(urls.turnHistogram(turn), (signal) => client.turnHistogram(turn, {}, signal))
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
  return <Histogram data={hist.data} bars={bars} tz={tz} range={zoom} cursorMs={cursorMs} realTime={zoom !== null} stacked hint="Drag across to zoom this turn" onBrush={onBrush} />
}
