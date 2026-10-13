import { useEffect, useMemo, useRef, useState } from 'react'
import { classifyError } from '../../../api/errors'
import type { GroupKey, Scope, SessionRow, SortDir, SortKey } from '../../../api/types'
import { useExplorerKeys } from '../keys'
import { useNav } from '../../../state/nav'
import { useUrlState } from '../../../state/url'
import { EmptyState } from '../../../ui/EmptyState'
import { ErrorCard } from '../../../ui/ErrorCard'
import { SkeletonRows } from '../../../ui/Skeleton'
import { ErrorRow, GroupBand, GroupFoldRow } from './GroupBlock'
import { ColumnBand, HeadLine } from './ListHeader'
import { ItemFrame } from './ItemFrame'
import { listColumns, moveCursor } from './list.logic'
import { NoResults } from './NoResults'
import { ListRow, SkeletonListRow, type RowCtx } from './SessionRow'
import { useSessionListModel } from './useSessionListModel'
import { useArrivalFade, useVirtualList } from './useVirtualList'
import { LIST_METRICS } from './virtual.logic'

/** The props F2a's contract fixes ("Contract with F2b"). `onHoverSession` gets the hovered or cursor row, not just its id: the day chart's footprint needs `day_split` (EXP-40). */
export interface SessionListProps {
  scope: Scope
  sort: SortKey
  dir: SortDir
  group: GroupKey | null
  onHoverSession: (row: SessionRow | null) => void
}

const BAND_H = 40

/**
 * CONTRACT (F2a: read before mounting). The list must sit inside a vertical scroll container that has no padding of its own (put page
 * padding on an inner wrapper, or the sticky column band stops that much below the top). It finds the nearest overflow-y auto or scroll
 * ancestor and throws if there is none. It registers its keys through `useExplorerKeys`; the parent must not register j/k itself.
 * `onHoverSession` gets the hovered row, and the cursor row on j/k, or null.
 *
 * EXP-47 to EXP-59: the session list. Flat or grouped, every in-scope session, rows drawn only while in view. It sits in the explorer
 * page's scroll container; its column band sticks to the top of that container (EXP-50).
 */
export function SessionList({ scope, sort, dir, group, onHoverSession }: SessionListProps) {
  const url = useUrlState()
  const nav = useNav()
  const model = useSessionListModel({ scope, sort, dir, group }, LIST_METRICS)
  const { container, view, scrollToItem, ready } = useVirtualList(model.layout, { stickyTop: BAND_H, overscan: 8 })
  const body = useRef<HTMLDivElement>(null)
  useArrivalFade(model.stale, body)

  const { setWindow } = model
  useEffect(() => setWindow(view.from, view.to), [setWindow, view.from, view.to])

  // EXP-54: the keyboard cursor. A new query puts it back to no selection.
  const [cursor, setCursor] = useState<number | null>(null)
  const queryKey = JSON.stringify([scope, sort, dir, group])
  useEffect(() => setCursor(null), [queryKey])
  const hover = useRef(onHoverSession)
  hover.current = onHoverSession
  const cursorRow = cursor === null ? null : model.navRow(cursor)
  const cursorSession = cursorRow ? cursorRow.session : null
  useEffect(() => {
    hover.current(cursorRow)
    // The row object is replaced when its page reloads; the session names the row.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursorSession])
  useEffect(() => {
    if (cursor === null) return
    const i = model.layout.itemOfNav(cursor)
    if (i >= 0) scrollToItem(i, BAND_H + (model.mode ? LIST_METRICS.group : 0))
    // Scroll when the cursor moves, not each time a page arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, ready])

  const open = (row: SessionRow) => nav.openBlade('session', { s: row.session })
  useExplorerKeys((e) => {
    if (e.key === 'j' || e.key === 'ArrowDown' || e.key === 'k' || e.key === 'ArrowUp') {
      const next = moveCursor(cursor, e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1, model.navCount)
      setCursor(next)
      if (next !== null) model.want(next)
      return true
    }
    if (e.key === 'Enter' && cursorRow) {
      open(cursorRow)
      return true
    }
    return false
  })

  const ctx: RowCtx = useMemo(
    () => ({
      tz: model.tz,
      singleDay: scope.from_day !== null && scope.from_day === scope.to_day,
      columns: listColumns(model.mode),
      group: model.mode,
      maxCost: model.maxCost,
      p90: model.p90,
      maxActivity: model.maxActivity,
    }),
    [model.tz, scope.from_day, scope.to_day, model.mode, model.maxCost, model.p90, model.maxActivity],
  )

  const head = <HeadLine total={model.total} group={group} onGroup={(g) => nav.replace({ group: g })} />
  if (model.status === 'loading') {
    return (
      <div data-testid="session-list">
        {head}
        <SkeletonRows count={8} rowHeight={LIST_METRICS.row} />
      </div>
    )
  }
  if (model.status === 'error' && model.error) {
    const kind = classifyError(model.error)
    return (
      <div data-testid="session-list">
        {head}
        {kind === 'no_store' ? (
          <EmptyState>No data yet.</EmptyState>
        ) : (
          <ErrorCard noun="sessions" message={kind === 'unreachable' ? undefined : model.error.message} unreachable={kind === 'unreachable'} onRetry={model.retry} />
        )}
      </div>
    )
  }
  if (model.total === 0) {
    return (
      <div data-testid="session-list">
        {head}
        <NoResults scope={scope} />
      </div>
    )
  }

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
              <ListRow
                row={it.row}
                prev={it.prev}
                ctx={ctx}
                open={it.row.session === url.s}
                cursor={it.nav === cursor}
                onOpen={open}
                onHover={(r) => hover.current(r ?? cursorRow)}
              />
            ) : (
              <SkeletonListRow height={LIST_METRICS.row} />
            ),
          ),
        )
        break
      case 'group':
        items.push(frame(<GroupBand group={it.group} mode={model.mode!} open={it.open} tz={model.tz} onToggle={() => model.toggleGroup(it.group.key)} />))
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

  return (
    <div data-testid="session-list">
      {head}
      <ColumnBand group={model.mode} sort={sort} dir={dir} onSort={(n) => nav.replace(n)} />
      <div ref={body}>
        <div ref={container} role="list" aria-label="Sessions" className="relative" style={{ height: L.height }}>
          {items}
          {view.pin && pinned && pinned.kind === 'group' ? (
            <div className="absolute inset-x-0 z-10" style={{ top: view.pin.top, height: LIST_METRICS.group }}>
              <GroupBand group={pinned.group} mode={model.mode!} open={pinned.open} tz={model.tz} onToggle={() => model.toggleGroup(pinned.group.key)} />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
