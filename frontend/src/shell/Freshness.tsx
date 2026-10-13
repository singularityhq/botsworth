import { useEffect, useState } from 'react'
import { classifyError } from '../api/errors'
import type { IngestStatus } from '../api/types'
import { useBrowserZone } from '../state/zone'
import { Icon } from '../ui/Icon'
import { Skeleton } from '../ui/Skeleton'
import { Tooltip } from '../ui/Tooltip'
import { freshnessText } from './freshness.logic'

function absoluteTime(ms: number, tz: string): string {
  return new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(ms)
}

function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs)
    return () => clearInterval(id)
  }, [everyMs])
  return now
}

/** EXP-8: "Ingested 12 min ago" in 14/400 ink3; the tooltip gives the absolute local time. While the first status request is out it
 *  shows a skeleton (SYS-54). A failed status request shows circle-alert and the status text in the error colour (SYS-8: glyph plus text). */
export function Freshness({ status, error }: { status: IngestStatus | undefined; error: unknown }) {
  const now = useNow(30_000)
  const zone = useBrowserZone()
  if (error !== null && status === undefined) {
    const text = classifyError(error) === 'unreachable' ? 'Server unreachable' : 'Status unavailable'
    return (
      <span className="inline-flex items-center gap-1.5 text-body text-error">
        <Icon name="circle-alert" tone="error" />
        {text}
      </span>
    )
  }
  if (status === undefined) return <Skeleton width={120} />
  const f = status.freshness
  const text = freshnessText(f.last_ingest_ms, now)
  const abs = f.last_ingest_ms === null ? 'No ingest has run' : absoluteTime(f.last_ingest_ms, zone)
  return (
    <Tooltip content={abs} side="bottom">
      <span className="text-body font-normal text-ink3">{text}</span>
    </Tooltip>
  )
}
