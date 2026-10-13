import type { SessionRow } from '../../../api/types'
import { GROUP_TOP, groupFold, groupOpen, type DisplayGroup, type GroupFold } from './list.logic'

/**
 * The list's rows laid out on one vertical axis, so only the rows in view are drawn (EXP-55). Every item has a fixed height; the flat
 * list is arithmetic, the grouped list a prefix-sum table. Pure: the component feeds it the scroll window.
 */

export interface Metrics {
  row: number
  group: number
  fold: number
}
/** SYS-33 row 44, SYS-37 group band 52, SYS-38 fold row 36. */
export const LIST_METRICS: Metrics = { row: 44, group: 52, fold: 36 }
/** SYS-36 compact row: 8 + 20 + 4 + 20 + 8 px and a 1 px rule; the rail's group band is 40 (EXP-62). */
export const RAIL_METRICS: Metrics = { row: 61, group: 40, fold: 36 }

/** Where an item sits in its group's body: the body has side rules, and its last item closes it with a bottom rule and radius. */
export type BodyPos = 'none' | 'mid' | 'last'

export type Item =
  | {
      kind: 'row'
      key: string
      /** null: not loaded yet, drawn as a skeleton row. */
      row: SessionRow | null
      /** Index among the rows the cursor can reach, or -1 for a skeleton. */
      nav: number
      /** The row above in the same run, for the SYS-34 weekday rule. */
      prev: SessionRow | null
      body: BodyPos
    }
  | { kind: 'group'; key: string; group: DisplayGroup; open: boolean }
  | { kind: 'fold'; key: string; group: DisplayGroup; fold: GroupFold; expanded: boolean; body: BodyPos }
  | { kind: 'error'; key: string; body: BodyPos; retry: string }

export interface Pin {
  /** Item index of the group header that is pinned. */
  index: number
  /** Container y of its top edge. */
  top: number
}

export interface Layout {
  count: number
  height: number
  offset(i: number): number
  size(i: number): number
  item(i: number): Item
  /** The item index of cursor row `nav`, or -1 when it is not drawn. */
  itemOfNav(nav: number): number
  /** The index of the item that covers container y (clamped to the ends). */
  indexAt(y: number): number
  /** SYS-37: the group header held at `line` (container y) while its group is under it. */
  pin(line: number): Pin | null
}

/** The flat list: `total` rows of one height. `rowAt` returns null for a row whose page is not loaded. */
export function flatLayout(total: number, rowAt: (i: number) => SessionRow | null, m: Metrics): Layout {
  return {
    count: total,
    height: total * m.row,
    offset: (i) => i * m.row,
    size: () => m.row,
    item(i) {
      const row = rowAt(i)
      return { kind: 'row', key: row ? row.session : `skeleton-${i}`, row, nav: i, prev: i > 0 ? rowAt(i - 1) : null, body: 'none' }
    },
    itemOfNav: (nav) => (nav >= 0 && nav < total ? nav : -1),
    indexAt: (y) => Math.max(0, Math.min(total - 1, Math.floor(y / m.row))),
    pin: () => null,
  }
}

export function itemSize(it: Item, m: Metrics): number {
  return it.kind === 'group' ? m.group : it.kind === 'fold' || it.kind === 'error' ? m.fold : m.row
}

/** The grouped list over a built item array. */
export function groupedLayout(items: readonly Item[], m: Metrics): Layout {
  const offsets: number[] = []
  const heads: number[] = []
  let y = 0
  items.forEach((it, i) => {
    offsets.push(y)
    if (it.kind === 'group') heads.push(i)
    y += itemSize(it, m)
  })
  const height = y
  const navItem = new Map<number, number>()
  items.forEach((it, i) => {
    if (it.kind === 'row' && it.nav >= 0) navItem.set(it.nav, i)
  })
  const indexAt = (at: number): number => {
    let lo = 0
    let hi = items.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (offsets[mid] <= at) lo = mid
      else hi = mid - 1
    }
    return Math.max(0, lo)
  }
  return {
    count: items.length,
    height,
    offset: (i) => offsets[i],
    size: (i) => itemSize(items[i], m),
    item: (i) => items[i],
    itemOfNav: (nav) => navItem.get(nav) ?? -1,
    indexAt,
    pin(line) {
      // The last header whose top edge is above the line.
      let found = -1
      for (let k = heads.length - 1; k >= 0; k--) {
        if (offsets[heads[k]] < line) {
          found = k
          break
        }
      }
      if (found < 0) return null
      const end = found + 1 < heads.length ? offsets[heads[found + 1]] : height
      if (line >= end) return null
      // The next header pushes this one up as it arrives.
      return { index: heads[found], top: Math.min(line, end - m.group) }
    },
  }
}

/** The rows `[start, end)` to draw for the container window `[y0, y1]`, with `overscan` extra items each side. */
export function visibleRange(layout: Layout, y0: number, y1: number, overscan: number): [number, number] {
  if (layout.count === 0 || y1 < 0 || y0 > layout.height) return [0, 0]
  const a = layout.indexAt(Math.max(0, y0))
  const b = layout.indexAt(Math.max(0, y1))
  return [Math.max(0, a - overscan), Math.min(layout.count, b + 1 + overscan)]
}

// ---- grouped item building (EXP-56 to EXP-59) ------------------------------

export interface GroupedInput {
  groups: readonly DisplayGroup[]
  /** Groups the user flipped from their default open state (EXP-59). */
  toggled: ReadonlySet<string>
  /** Groups whose fold row is open (SYS-38). */
  expanded: ReadonlySet<string>
  /** All rows of an expanded group once fetched (API-12 `group_key`), or an error. */
  groupRows: (key: string) => { rows: SessionRow[] } | { error: true } | undefined
}

/**
 * Header, then the group's rows under the fold rule: the top 5, a fold row, and once expanded all rows. An expanded group whose rows are
 * still loading shows skeleton rows in their place; a failed fetch shows one error row. The "Small projects" group already holds all its
 * rows, so its fold needs no fetch. Rows are numbered in reading order for the cursor.
 */
export function groupedItems(inp: GroupedInput): { items: Item[]; navRows: SessionRow[] } {
  const items: Item[] = []
  const navRows: SessionRow[] = []
  for (const g of inp.groups) {
    const open = groupOpen(g, inp.toggled)
    items.push({ kind: 'group', key: `g:${g.key}`, group: g, open })
    if (!open) continue
    const expanded = inp.expanded.has(g.key)
    const fold = groupFold(g, expanded)
    const body: Item[] = []
    let rows: SessionRow[] | null
    let failed = false
    if (!expanded) rows = g.rows.slice(0, GROUP_TOP)
    else if (g.small) rows = g.rows
    else {
      const got = inp.groupRows(g.key)
      if (got === undefined) rows = null
      else if ('error' in got) {
        rows = g.rows.slice(0, GROUP_TOP)
        failed = true
      } else rows = got.rows
    }
    if (rows === null) {
      // Loading: the top rows are in hand, the rest are skeletons.
      rows = g.rows.slice(0, GROUP_TOP)
    }
    const loadingMore = expanded && !g.small && inp.groupRows(g.key) === undefined
    let prev: SessionRow | null = null
    for (const row of rows) {
      body.push({ kind: 'row', key: row.session, row, nav: navRows.length, prev, body: 'mid' })
      navRows.push(row)
      prev = row
    }
    if (loadingMore) {
      for (let i = 0; i < Math.min(fold.hidden, GROUP_TOP); i++) {
        body.push({ kind: 'row', key: `skeleton-${g.key}-${i}`, row: null, nav: -1, prev: null, body: 'mid' })
      }
    }
    if (failed) body.push({ kind: 'error', key: `error-${g.key}`, body: 'mid', retry: g.key })
    if (fold.label !== '') body.push({ kind: 'fold', key: `fold-${g.key}`, group: g, fold, expanded, body: 'mid' })
    if (body.length > 0) {
      const last = body[body.length - 1]
      body[body.length - 1] = { ...last, body: 'last' } as Item
    }
    items.push(...body)
  }
  return { items, navRows }
}
