import { useEffect, useMemo, useRef } from 'react'
import { classifyError } from '../../../api/errors'
import type { SessionRow } from '../../../api/types'
import { useExplorerKeys } from '../keys'
import { useNav } from '../../../state/nav'
import { useUrlState } from '../../../state/url'
import { EmptyState } from '../../../ui/EmptyState'
import { ErrorCard } from '../../../ui/ErrorCard'
import { Icon } from '../../../ui/Icon'
import { KeyCap } from '../../../ui/KeyCap'
import { SkeletonRows } from '../../../ui/Skeleton'
import { ErrorRow, GroupFoldRow, RailGroupBand } from '../list/GroupBlock'
import { ItemFrame } from '../list/ItemFrame'
import { moveCursor, openIndex, railPosition, swapSessionPatch } from '../list/list.logic'
import { NoResults } from '../list/NoResults'
import type { SessionListProps } from '../list/SessionList'
import { useSessionListModel } from '../list/useSessionListModel'
import { useArrivalFade, useVirtualList } from '../list/useVirtualList'
import { RAIL_METRICS } from '../list/virtual.logic'
import { RailRow, SkeletonRailRow, type RailCtx } from './RailRow'

export type RailProps = SessionListProps

/** EXP-61: the search field of the rail, bound to the explorer's search text (EXP-17). The URL updates on every keystroke (FE-5). */
function RailSearch() {
  const url = useUrlState()
  const nav = useNav()
  return (
    <label className="relative mt-3 block">
      <Icon name="search" tone="muted" className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2" />
      <input
        value={url.q}
        onChange={(e) => nav.replace({ q: e.target.value })}
        placeholder="Search sessions"
        aria-label="Search sessions"
        data-focus-quiet
        className="h-8 w-full rounded-control border border-edge bg-l1 pr-8 pl-8 text-body text-ink1 placeholder:text-ink3 focus:border-struct-line"
      />
      {url.q !== '' ? (
        <button type="button" aria-label="Clear search" onClick={() => nav.replace({ q: '' })} className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded-chip p-0.5">
          <Icon name="x" tone="muted" className="hover:text-ink1" />
        </button>
      ) : null}
    </label>
  )
}

/**
 * EXP-60 to EXP-66: the explorer as the 400 px rail while a session blade is open. The same sessions, order, grouping and folds as the
 * list. The cursor is the open session; j and k replace it with the next or previous row (EXP-63).
 */
export function Rail({ scope, sort, dir, group, onHoverSession }: RailProps) {
  const url = useUrlState()
  const nav = useNav()
  const model = useSessionListModel({ scope, sort, dir, group }, RAIL_METRICS)
  const { container, view, scrollToItem, ready } = useVirtualList(model.layout, { stickyTop: 0, overscan: 8 })
  const body = useRef<HTMLDivElement>(null)
  useArrivalFade(model.stale, body)

  const { setWindow } = model
  useEffect(() => setWindow(view.from, view.to), [setWindow, view.from, view.to])

  // EXP-64/65: the cursor sits on the open session. When that session is not among the rows (it left the scope, EXP-65, or its page has not loaded) there is none.
  const here = useMemo(
    () => openIndex(url.s, model.navRow, model.navCount),
    [url.s, model.navRow, model.navCount],
  )
  const hover = useRef(onHoverSession)
  hover.current = onHoverSession

  const select = (row: SessionRow) => {
    if (row.session !== url.s) nav.replace(swapSessionPatch(row.session))
  }
  useExplorerKeys((e) => {
    if (e.key !== 'j' && e.key !== 'ArrowDown' && e.key !== 'k' && e.key !== 'ArrowUp') return false
    const next = moveCursor(here, e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1, model.navCount)
    if (next === null) return true
    model.want(next)
    const row = model.navRow(next)
    // A page that has not arrived yet: the cursor target is requested and the key does nothing until it shows.
    if (row) select(row)
    return true
  })

  // Bring the open row into view when it changes (a j/k step, or the rail opening on a session far down).
  useEffect(() => {
    if (here === null) return
    const i = model.layout.itemOfNav(here)
    if (i >= 0) scrollToItem(i, model.mode ? RAIL_METRICS.group : 0)
    // Scroll when the open row changes, not each time a page arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [here, ready])

  const ctx: RailCtx = useMemo(
    () => ({ tz: model.tz, singleDay: scope.from_day !== null && scope.from_day === scope.to_day, group: model.mode, p90: model.p90, maxActivity: model.maxActivity }),
    [model.tz, scope.from_day, scope.to_day, model.mode, model.p90, model.maxActivity],
  )

  let content: React.ReactNode
  if (model.status === 'loading') {
    content = <SkeletonRows count={8} rowHeight={RAIL_METRICS.row} />
  } else if (model.status === 'error' && model.error) {
    const kind = classifyError(model.error)
    content =
      kind === 'no_store' ? (
        <EmptyState>No data yet.</EmptyState>
      ) : (
        <ErrorCard noun="sessions" message={kind === 'unreachable' ? undefined : model.error.message} unreachable={kind === 'unreachable'} onRetry={model.retry} />
      )
  } else if (model.total === 0) {
    content = <NoResults scope={scope} />
  } else {
    const L = model.layout
    const items = []
    for (let i = view.from; i < Math.min(view.to, L.count); i++) {
      const it = L.item(i)
      const frame = (child: React.ReactNode) => (
        <ItemFrame key={it.key} top={L.offset(i)} height={L.size(i)} body={it.kind === 'group' ? 'none' : it.body}>
          {child}
        </ItemFrame>
      )
      switch (it.kind) {
        case 'row':
          items.push(
            frame(
              it.row ? (
                <RailRow
                  row={it.row}
                  prev={it.prev}
                  ctx={ctx}
                  open={it.row.session === url.s}
                  cursor={it.nav === here}
                  onSelect={select}
                  onHover={(r) => hover.current(r)}
                />
              ) : (
                <SkeletonRailRow />
              ),
            ),
          )
          break
        case 'group':
          items.push(frame(<RailGroupBand group={it.group} mode={model.mode!} open={it.open} onToggle={() => model.toggleGroup(it.group.key)} />))
          break
        case 'fold':
          items.push(frame(<GroupFoldRow fold={it.fold} expanded={it.expanded} onToggle={() => model.toggleFold(it.group.key)} />))
          break
        case 'error':
          items.push(frame(<ErrorRow message="Couldn't load these sessions" onRetry={() => model.retryGroup(it.retry)} />))
          break
      }
    }
    const pinned = view.pin ? L.item(view.pin.index) : null
    content = (
      <div ref={container} role="list" aria-label="Sessions" className="relative" style={{ height: L.height }}>
        {items}
        {view.pin && pinned && pinned.kind === 'group' ? (
          <div className="absolute inset-x-0 z-10" style={{ top: view.pin.top, height: RAIL_METRICS.group }}>
            <RailGroupBand group={pinned.group} mode={model.mode!} open={pinned.open} onToggle={() => model.toggleGroup(pinned.group.key)} />
          </div>
        ) : null}
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col" data-testid="rail">
      <div className="shrink-0 px-4 pt-4 pb-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-body font-semibold text-ink1">Sessions</h2>
          <span className="fig text-body text-ink3" aria-label="Position in the list">
            {railPosition(here, model.navCount)}
          </span>
        </div>
        <RailSearch />
      </div>
      <div ref={body} className="min-h-0 flex-1 px-4" style={{ overflowY: 'auto' }}>
        {content}
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-rule-soft px-4 py-3 text-body text-ink3">
        <KeyCap>j</KeyCap>
        <KeyCap>k</KeyCap>
        <span>move</span>
        <span aria-hidden="true">·</span>
        <KeyCap>↵</KeyCap>
        <span>open</span>
        <span aria-hidden="true">·</span>
        <KeyCap>esc</KeyCap>
        <span>back</span>
      </div>
    </div>
  )
}
