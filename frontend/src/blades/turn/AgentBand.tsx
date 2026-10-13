import { client, urls } from '../../api/client'
import type { AgentRowPage } from '../../api/types'
import { useApi } from '../../api/useApi'
import { useEffect } from 'react'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { Skeleton } from '../../ui/Skeleton'
import type { PageState } from './agentPages'
import { BAND_PAGE } from './rowList.logic'
import { BAND_ERROR_H, ROW_H } from './turn.logic'

/**
 * Loads one page of an agent's rows (API-32) and reports it. One loader per open agent and page, rendered with no output, so the request goes
 * through `useApi` (FE-4: cached, shared, aborted when the agent is folded).
 */
export function AgentLoader({ agent, offset, onState }: { agent: string; offset: number; onState: (agent: string, offset: number, s: PageState) => void }) {
  const r = useApi<AgentRowPage>(urls.agentRows(agent, { offset, limit: BAND_PAGE }), (signal) => client.agentRows(agent, { offset, limit: BAND_PAGE }, signal))
  const status = r.error ? 'error' : r.data ? 'ready' : 'loading'
  useEffect(() => {
    onState(agent, offset, {
      status,
      rows: r.data?.rows ?? [], // FALLBACK-OK: TRN-35 (no rows until the page arrives; the band shows its skeleton)
      total: r.data?.total ?? 0, // FALLBACK-OK: TRN-35 (same)
      message: r.error?.message ?? null, // FALLBACK-OK: TRN-35 (no message without a failure)
      retry: r.retry,
    })
  }, [agent, offset, status, r.data, r.error, r.retry, onState])
  return null
}

/** TRN-35 (D38 M8): skeleton rows inside the band while the agent's rows load. */
export function BandSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col" style={{ height: ROW_H * 3 }}>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center border-b border-rule-soft px-5" style={{ height: ROW_H }}>
          <Skeleton height={14} width={`${45 + i * 15}%`} />
        </div>
      ))}
    </div>
  )
}

/** TRN-35: the SYS-56 error card inside the band, with Retry. */
export function BandError({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div className="overflow-y-auto" style={{ height: BAND_ERROR_H }}>
      <ErrorCard noun="the agent's rows" message={message ?? undefined /* FALLBACK-OK: SYS-56 (no server message: the title stands alone) */} onRetry={onRetry} />
    </div>
  )
}

/** TRN-35 (D38 M8): after 500 rows, a fold row "Show N more" loads the next page. */
export function ShowMore({ n, cost, onMore, label }: { n: number; cost?: React.ReactNode; onMore: () => void; label?: string }) {
  return (
    <button type="button" onClick={onMore} className="flex w-full items-center gap-1.5 border-b border-rule-soft px-5 text-body text-struct transition-colors duration-150 hover:bg-l3" style={{ height: ROW_H }}>
      <Icon name="list-collapse" tone="struct" />
      <span className="flex-1">{label ?? /* FALLBACK-OK: SYS-38 (the default fold wording) */ `Show ${n} more`}</span>
      {cost}
    </button>
  )
}
