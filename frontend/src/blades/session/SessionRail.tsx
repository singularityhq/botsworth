import { useEffect, useRef } from 'react'
import type { SessionDetail } from '../../api/types'
import { Clock, Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { ProjectDot } from '../../ui/ProjectDot'
import { CompactRow } from '../../ui/Row'
import { SkeletonRows } from '../../ui/Skeleton'
import { Flags } from './TurnRow'
import { findRow, RAIL_WAITING_MIN_MS, revealInBox, turnFlags, waitingShown, type TurnEntry } from './turnList.logic'
import { matchCount } from './sessionSearch'
import type { FindResult } from '../../api/types'

interface RailProps {
  session: SessionDetail['session'] | undefined
  entries: TurnEntry[]
  loading: boolean
  tz: string
  find: string
  search: FindResult | undefined
  /** Op id of the turn open in the turn blade. */
  openKey: string | null
  cursor: string | null
  onOpenTurn: (key: string) => void
}

/** SES-4 (400 px): the session's compressed form while the turn blade is open. Head, then one compact row per numbered turn the search and range keep. */
export function SessionRail({ session: s, entries, loading, tz, find, search, openKey, cursor, onOpenTurn }: RailProps) {
  const list = useRef<HTMLDivElement>(null)

  // SES-4: the rail's open row follows the turn blade and scrolls into view. Row heights keep changing after the
  // first reveal (web font swap, the strip's width transition), so the reveal repeats on each resize of the box or a
  // row until the user scrolls the rail themselves (wheel or touch), or the open turn changes.
  useEffect(() => {
    const box = list.current
    if (openKey === null || !box) return
    let armed = true
    const reveal = () => {
      const row = findRow(box, openKey)
      // Not `scrollIntoView`: it scrolls every ancestor too, and pulled the blade strip back to the rail (FX 8).
      if (armed && row) revealInBox(box, row)
    }
    const disarm = () => {
      armed = false
    }
    reveal()
    const ro = new ResizeObserver(reveal)
    ro.observe(box)
    for (const child of Array.from(box.children)) ro.observe(child)
    box.addEventListener('wheel', disarm, { passive: true })
    box.addEventListener('touchmove', disarm, { passive: true })
    return () => {
      ro.disconnect()
      box.removeEventListener('wheel', disarm)
      box.removeEventListener('touchmove', disarm)
    }
  }, [openKey, entries.length])

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-hair p-4">
        <div className="flex items-center gap-2 text-ink3">
          {s ? (
            <>
              <ProjectDot slot={s.color_slot} />
              <span className="min-w-0 flex-1 truncate">{s.project_name}</span>
              <Money value={s.cost.cost_usd} lowerBound={s.cost.cost_lower_bound} nUnpriced={s.cost.n_unpriced} weight={400} />
              <Duration ms={s.wall_ms} ink="ink3" />
            </>
          ) : null}
        </div>
        <div className="mt-1 truncate font-semibold text-ink1" title={s?.name}>
          {s?.name}
        </div>
      </div>
      <div ref={list} className="min-h-0 flex-1 overflow-y-auto" aria-label="Turns">
        {loading ? (
          <SkeletonRows count={8} rowHeight={56} />
        ) : (
          entries.map((e) => {
            const r = e.row
            const wait = waitingShown(r.waiting_ms, RAIL_WAITING_MIN_MS)
            return (
              <CompactRow
                key={e.key}
                data-turn-key={e.key}
                open={openKey === e.key}
                cursor={cursor === e.key}
                role="button"
                tabIndex={-1}
                style={{ padding: '8px 16px 8px 12px' }}
                onClick={() => onOpenTurn(e.key)}
                nameTitle={r.label}
                name={
                  <>
                    <span className="fig inline-block w-6 font-normal text-ink3 group-data-[open=true]/row:text-ink3">{e.ix}</span>
                    {r.label}
                  </>
                }
                figure={<Money value={r.cost_usd} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} weight={400} />}
                when={r.start_ms !== null ? <Clock ms={r.start_ms} tz={tz} /> : <span />}
                meta={
                  <>
                    <Duration ms={r.wall_ms} ink="ink3" />
                    {wait !== null ? (
                      <span className="inline-flex items-center gap-1">
                        <Icon name="hourglass" />
                        <Duration ms={wait} ink="ink3" />
                      </span>
                    ) : null}
                    <Flags flags={turnFlags(r, find === '' ? null : matchCount(search, r.turn))} find={find} />
                  </>
                }
              />
            )
          })
        )}
      </div>
    </div>
  )
}
