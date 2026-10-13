import type { Facets, Provider } from '../../api/types'

/** Which facet values the filter popover lists (EXP-21 to EXP-23). Counts of 0 stay behind "Show all (n)"; a selected value always shows. */
export interface FacetRow {
  value: string
  n: number
}

export interface FacetList {
  rows: FacetRow[]
  /** Values with a count of 0 that are not selected and are hidden. */
  hiddenCount: number
}

export function visibleFacets(all: readonly FacetRow[], selected: readonly string[], showAll: boolean, search = ''): FacetList {
  const needle = search.trim().toLowerCase()
  const matched = needle === '' ? all : all.filter((r) => r.value.toLowerCase().includes(needle))
  const hidden = matched.filter((r) => r.n === 0 && !selected.includes(r.value))
  return { rows: showAll ? [...matched] : matched.filter((r) => !hidden.includes(r)), hiddenCount: hidden.length }
}

/** Providers present in the period, plus any selected one (EXP-21). */
export function presentProviders(f: Facets, selected: readonly string[]): Facets['providers'] {
  return f.providers.filter((p) => p.n > 0 || selected.includes(p.value as Provider))
}
