import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { client, urls } from '../../../api/client'
import { ApiError, NetError } from '../../../api/errors'
import type { GroupKey, Scope, SessionPage, SessionRow, SortDir, SortKey } from '../../../api/types'
import { useApi } from '../../../api/useApi'
import { useBrowserZone } from '../../../state/zone'
import { displayGroups, pageConsistent, PAGE, wantedPages } from './list.logic'
import { useFetchMap } from './useFetchMap'
import { flatLayout, groupedItems, groupedLayout, type Layout, type Metrics } from './virtual.logic'

export interface ListQuery {
  scope: Scope
  sort: SortKey
  dir: SortDir
  group: GroupKey | null
}

export interface ListModel {
  /** `loading`: first load, nothing to show. `error`: the first page failed. `ready`: rows (possibly of the previous scope, see `stale`). */
  status: 'loading' | 'error' | 'ready'
  error: ApiError | NetError | null
  retry: () => void
  /** EXP-78: the rows belong to the previous query until the new page arrives. */
  stale: boolean
  /** Sessions in scope (API-12 `total`). */
  total: number
  tz: string
  /** How the rows on screen are grouped. It follows the data, so it lags `q.group` until the new page arrives (EXP-78). */
  mode: GroupKey | null
  /** `stats.cost_p90` and `stats.max_activity_tok`: shared by every row (EXP-49, SYS-40). */
  p90: number | null
  maxActivity: number
  maxCost: number
  layout: Layout
  /** The rows the cursor can reach, in reading order: every row when flat, the shown rows when grouped. */
  navCount: number
  navRow: (i: number) => SessionRow | null
  /** Tell the model which items are on screen, so it loads the pages under them (API-12). */
  setWindow: (from: number, to: number) => void
  /** Ask for the page that holds nav row `i` (the cursor moved there). */
  want: (i: number) => void
  toggleGroup: (key: string) => void
  toggleFold: (key: string) => void
  retryGroup: (key: string) => void
  /** Retry the page that holds this row. */
  retryPage: (nav: number) => void
}

/**
 * The data of the session list and the rail, one model for both (EXP-60: the same sessions, order, grouping and folds). Flat: page 0 and
 * further pages of 200 as the window reaches them. Grouped: every group with its top rows, and a group's full rows once its fold opens.
 * The URL is the only source of the query (FE-6); this keeps no copy of it.
 */
export function useSessionListModel(q: ListQuery, metrics: Metrics): ListModel {
  const firstUrl = urls.explorerSessions(q.scope, { sort: q.sort, dir: q.dir, group: q.group ?? undefined })
  const first = useApi<SessionPage>(firstUrl, (signal) => client.explorerSessions(q.scope, { sort: q.sort, dir: q.dir, group: q.group ?? undefined }, signal))
  const page0 = first.data
  const tz = useBrowserZone()
  // EXP-78: until the new first page arrives the screen keeps the old rows. Every request that belongs to those rows (further pages,
  // a group's rows) must use the query they came from, or rows of another sort would land in the old list.
  const settled = useRef(q)
  if (!first.stale) settled.current = q
  const { scope, sort, dir, group } = first.stale ? settled.current : q
  const grouped = group !== null
  // Names the query of the rows on screen; the memos below depend on it.
  const shownUrl = urls.explorerSessions(scope, { sort, dir, group: group ?? undefined })

  const [win, setWin] = useState<[number, number]>([0, 0])
  const [target, setTarget] = useState<number[]>([])
  const [toggled, setToggled] = useState<ReadonlySet<string>>(new Set())
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())

  const setWindow = useCallback((from: number, to: number) => setWin((w) => (w[0] === from && w[1] === to ? w : [from, to])), [])
  const want = useCallback((i: number) => setTarget((t) => (t.length === 1 && t[0] === i ? t : [i])), [])
  const flip = (set: ReadonlySet<string>, key: string) => {
    const next = new Set(set)
    if (!next.delete(key)) next.add(key)
    return next
  }
  const toggleGroup = useCallback((key: string) => setToggled((s) => flip(s, key)), [])
  const toggleFold = useCallback((key: string) => setExpanded((s) => flip(s, key)), [])

  // Flat mode: the pages under the window and the cursor target. Page 0 comes from `useApi`.
  const total = page0?.total ?? 0
  const pages = grouped || !page0 ? [] : wantedPages(win[0], win[1], total, target).filter((p) => p > 0)
  const pageUrl = (p: number) => urls.explorerSessions(scope, { sort, dir, offset: p * PAGE })
  // Group mode: the full rows of each expanded group other than "Small projects" (whose rows are all in hand).
  const groupReqs = useMemo(
    () => (grouped && page0?.groups ? page0.groups.filter((g) => expanded.has(g.key) && g.n_sessions > g.rows.length) : []),
    [grouped, page0, expanded],
  )
  const groupUrl = (key: string, n: number) =>
    urls.explorerSessions(scope, { sort, dir, group: group ?? undefined, group_key: key, limit: Math.min(n, 1000) })

  const extra = useFetchMap<SessionPage>([
    ...pages.map((p) => ({ url: pageUrl(p), fetch: (signal: AbortSignal) => client.explorerSessions(scope, { sort, dir, offset: p * PAGE }, signal) })),
    ...groupReqs.map((g) => ({
      url: groupUrl(g.key, g.n_sessions),
      fetch: (signal: AbortSignal) =>
        client.explorerSessions(scope, { sort, dir, group: group ?? undefined, group_key: g.key, limit: Math.min(g.n_sessions, 1000) }, signal),
    })),
  ])

  // QRY-16, FE-7: a page that disagrees with page 0 means the store moved; page 0 is fetched again, twice at most.
  const tries = useRef(0)
  const staleLoaded = page0 ? pages.filter((p) => {
    const body = extra.data.get(pageUrl(p))
    return body !== undefined && !pageConsistent(page0, body)
  }) : []
  const inconsistent = staleLoaded.length > 0
  const retryFirst = first.retry
  useEffect(() => {
    if (!inconsistent) {
      tries.current = 0
      return
    }
    if (tries.current < 2) {
      tries.current += 1
      retryFirst()
    }
  }, [inconsistent, retryFirst])
  const changed = inconsistent && tries.current >= 2

  const flatRowAt = useCallback(
    (i: number): SessionRow | null => {
      if (!page0 || i < 0 || i >= page0.total) return null
      const p = Math.floor(i / PAGE)
      if (p === 0) return page0.rows[i] ?? null
      const body = extra.data.get(pageUrl(p))
      return body && pageConsistent(page0, body) ? (body.rows[i % PAGE] ?? null) : null
    },
    // pageUrl closes over the query only, which `shownUrl` already names.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [page0, extra.data, shownUrl],
  )

  const built = useMemo(() => {
    if (!page0) return null
    if (!grouped) return { layout: flatLayout(page0.total, flatRowAt, metrics), navCount: page0.total, navRow: flatRowAt }
    if (page0.groups === null) throw new Error('useSessionListModel: grouped request answered without groups (API-12)')
    const dg = displayGroups(page0.groups, group, sort, dir)
    const { items, navRows } = groupedItems({
      groups: dg,
      toggled,
      expanded,
      groupRows: (key) => {
        const g = dg.find((x) => x.key === key)
        // The server's `rows` are the group's first rows; the fetch answers the rest.
        const u = g && groupUrl(key, g.n_sessions)
        const body = u ? extra.data.get(u) : undefined
        if (body) return { rows: body.rows }
        if (u && extra.errors.has(u)) return { error: true as const }
        return undefined
      },
    })
    return { layout: groupedLayout(items, metrics), navCount: navRows.length, navRow: (i: number) => navRows[i] ?? null }
    // groupUrl closes over the query only, which `shownUrl` already names.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page0, grouped, flatRowAt, metrics, toggled, expanded, extra.data, extra.errors, shownUrl])

  const retryGroup = useCallback(
    (key: string) => {
      const g = page0?.groups?.find((x) => x.key === key)
      if (g) extra.retry(groupUrl(key, g.n_sessions))
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [page0, extra.retry, shownUrl],
  )
  const retryPage = useCallback(
    (nav: number) => extra.retry(pageUrl(Math.floor(nav / PAGE))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [extra.retry, shownUrl],
  )

  const error = changed ? new ApiError(409, 'list_changed', 'The session list changed while it was loading') : first.error
  const status: ListModel['status'] = error ? 'error' : built && page0 ? 'ready' : 'loading'
  // An empty layout stands in until page 0 arrives; `status` says nothing is shown yet.
  const none = useMemo(() => flatLayout(0, () => null, metrics), [metrics])
  return {
    status,
    error,
    retry: () => {
      tries.current = 0
      retryFirst()
    },
    stale: first.stale,
    total,
    tz,
    mode: group,
    p90: page0?.stats.cost_p90 ?? null, // FALLBACK-OK: interfaces.md API-12 (cost_p90 is null when no in-scope row has a cost)
    maxActivity: page0?.stats.max_activity_tok ?? 0, // FALLBACK-OK: interfaces.md API-12 (no page yet: nothing is drawn)
    maxCost: page0?.stats.max_cost_usd ?? 0, // FALLBACK-OK: interfaces.md API-12 (no page yet: nothing is drawn)
    layout: built?.layout ?? none,
    navCount: built?.navCount ?? 0,
    navRow: built?.navRow ?? (() => null),
    setWindow,
    want,
    toggleGroup,
    toggleFold,
    retryGroup,
    retryPage,
  }
}
