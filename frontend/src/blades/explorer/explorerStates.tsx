import { classifyError } from '../../api/errors'
import type { ApiError, NetError } from '../../api/errors'
import type { Day, PeriodSummary } from '../../api/types'
import { EmptyState } from '../../ui/EmptyState'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { allTimeMatchText, emptyPeriodText, nearbyDayText, type FilterToken } from './explorer.logic'
import { FilterTokens } from './ScopeBar'

/** FE-8: what a region shows for a failed request. A missing or old store reads "No data yet." (EXP-82); a failure shows the error
 *  card with the server's message (EXP-81); an unreachable server shows the FE-8 wording (EXP-83). */
export function RegionError({ error, noun, onRetry }: { error: ApiError | NetError; noun: string; onRetry: () => void }) {
  const kind = classifyError(error)
  if (kind === 'no_store' || kind === 'schema_mismatch') return <EmptyState icon="database">No data yet.</EmptyState>
  // ErrorCard carries its own 16 px margin (it is built for a full pane); inside a region the card sits flush with the region.
  return (
    <div className="-m-4 max-w-[560px]">
      <ErrorCard noun={noun} message={kind === 'unreachable' ? undefined : error.message} unreachable={kind === 'unreachable'} onRetry={onRetry} />
    </div>
  )
}

/** EXP-82: a store that cannot answer. */
export function NoData() {
  return <EmptyState icon="database">No data yet.</EmptyState>
}

/** EXP-79: search or a filter is on and nothing matches. The chart stays; the panel and the list header are hidden. */
export function NoResults({
  q, tokens, onRemove, onClear, allTime, onShowAllTime,
}: {
  q: string
  tokens: readonly FilterToken[]
  onRemove: (t: FilterToken) => void
  onClear: () => void
  /** Sessions matching the search in all time; undefined while unknown. */
  allTime: number | null | undefined
  onShowAllTime: () => void
}) {
  const text = q.trim()
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <Icon name="search-x" size={24} tone="muted" />
      <h2 className="text-lead font-semibold text-ink1">No sessions match</h2>
      {tokens.length > 0 ? (
        <div className="flex flex-wrap justify-center gap-2">
          <FilterTokens tokens={tokens} onRemove={onRemove} projectSlot={() => null} />
        </div>
      ) : null}
      <button type="button" onClick={onClear} className="h-8 rounded-control border border-struct-line bg-struct-soft px-3 text-body text-struct hover:border-struct">
        Clear filters
      </button>
      {text !== '' && allTime !== null && allTime !== undefined ? (
        <p className="text-body text-ink2">
          {allTimeMatchText(text, allTime)}
          {allTime > 0 ? (
            <>
              {' · '}
              <button type="button" onClick={onShowAllTime} className="text-struct hover:underline">
                Show
              </button>
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  )
}

/** EXP-80: nothing in the period and nothing narrows it. Up to two buttons jump to the nearest days with sessions. */
export function EmptyPeriod({ from, to, empty, onPick }: { from: Day; to: Day; empty: NonNullable<PeriodSummary['empty']> | null; onPick: (day: Day) => void }) {
  const near = [empty?.prev_day ?? null, empty?.next_day ?? null].filter((d): d is { day: Day; n_sessions: number } => d !== null)
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <Icon name="calendar-x" size={24} tone="muted" />
      <h2 className="text-lead font-semibold text-ink1">{emptyPeriodText(from, to)}</h2>
      {near.length > 0 ? (
        <div className="flex flex-wrap justify-center gap-2">
          {near.map((d) => (
            <button
              key={d.day}
              type="button"
              onClick={() => onPick(d.day)}
              className="h-8 rounded-control border border-struct-line bg-struct-soft px-3 text-body text-struct hover:border-struct"
            >
              {nearbyDayText(d.day, d.n_sessions)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
