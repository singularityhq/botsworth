import { useMemo } from 'react'
import { client, urls } from '../../api/client'
import type { ApiError, NetError } from '../../api/errors'
import type { Day, DaySeries, IngestStatus, PeriodSummary, Scope } from '../../api/types'
import { useApi, useDebounced, type ApiState } from '../../api/useApi'
import { chartWindow, resolvePeriod, todayIn, type ChartWindow, type DayRange } from '../../state/period'
import { useBrowserZone } from '../../state/zone'
import { toScope, useUrlState, type UrlState } from '../../state/url'
import { panelScope } from './projects.logic'

/** FE-9 [D50]: today's date comes from the browser zone, so the period resolves at once; only the data requests wait for the status call (SYS-57). FE-5: the search text in a request is the
 *  text debounced by 150 ms; the URL itself follows every keystroke. */
export interface ExplorerScope {
  url: UrlState
  status: ApiState<IngestStatus>
  today: Day
  /** The period as days; all time has both ends null. */
  range: DayRange
  scope: Scope
  /** False while the status is unknown or the store cannot answer (SYS-57); the data hooks then fetch nothing. */
  ready: boolean
}

export function useExplorerScope(): ExplorerScope {
  const url = useUrlState()
  const status = useApi<IngestStatus>('/api/ingest/status', (signal) => client.ingestStatus(signal))
  const q = useDebounced(url.q, 150)
  const tz = useBrowserZone()
  const today = todayIn(tz, Date.now())
  const scope = useMemo(() => ({ ...toScope(url, today), q }), [url, today, q])
  const range = useMemo(() => resolvePeriod(url.period, today), [url.period, today])
  return { url, status, today, range, scope, ready: status.data?.store_state === 'ready' }
}

export interface ExplorerData {
  /** Totals, projects and empty-state data under the full scope. */
  summary: ApiState<PeriodSummary>
  /** The project panel: the same under the scope without the project filter (EXP-43, QRY-8). It shares the summary's request when no project filter is on. */
  panel: ApiState<PeriodSummary>
  days: ApiState<DaySeries>
  /** The chart window, or null while it cannot be known (all time before the summary arrives). */
  window: ChartWindow | null
}

export function useExplorerData(s: ExplorerScope): ExplorerData {
  const { scope, ready, url, today } = s
  const on = ready
  const summary = useApi<PeriodSummary>(on ? urls.explorerSummary(scope) : null, (signal) => client.explorerSummary(scope, signal))
  const panel = useApi<PeriodSummary>(on ? urls.explorerSummary(panelScope(scope)) : null, (signal) => client.explorerSummary(panelScope(scope), signal))
  const extent = summary.data ?? panel.data
  const first = extent?.first_day ?? null
  const last = extent?.last_day ?? null
  const window = useMemo<ChartWindow | null>(() => {
    if (url.period.kind === 'preset' && url.period.preset === 'all' && (first === null || last === null)) return null
    return chartWindow(url.period, today, { first_day: first, last_day: last })
  }, [url.period, today, first, last])
  const days = useApi<DaySeries>(on && window ? urls.explorerDays(scope, window) : null, (signal) => client.explorerDays(scope, window!, signal))
  return { summary, panel, days, window }
}

export type RequestError = ApiError | NetError
