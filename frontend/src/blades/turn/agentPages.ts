import type { AnyRow } from '../../api/types'
import { BAND_PAGE, type AgentPage, type BandState } from './rowList.logic'

/** One API-32 page of an agent's rows, as the loader reports it. */
export interface PageState {
  status: 'loading' | 'error' | 'ready'
  rows: readonly AnyRow[]
  total: number
  message: string | null
  retry: () => void
}

export const pageKey = (agent: string, offset: number): string => `${agent}@${offset}`

export interface Assembled {
  pages: Map<string, AgentPage>
  bands: Map<string, BandState>
  /** Retry callbacks of the failed first pages, by agent. */
  retries: Map<string, () => void>
}

/**
 * TRN-35 (D38 M8): from the pages the loaders reported and the number of pages asked for per open agent, the rows to splice into the list and
 * the band state each agent shows. A first page that is loading is a skeleton; one that failed is an error card with Retry. Later pages add
 * their rows when they arrive; until then (or if one failed) the band shows "Show N more" for what is missing.
 */
export function assemblePages(states: ReadonlyMap<string, PageState>, asked: ReadonlyMap<string, number>): Assembled {
  const out: Assembled = { pages: new Map(), bands: new Map(), retries: new Map() }
  for (const [agent, count] of asked) {
    const first = states.get(pageKey(agent, 0))
    if (first === undefined || first.status === 'loading') {
      out.bands.set(agent, { status: 'loading' })
      continue
    }
    if (first.status === 'error') {
      out.bands.set(agent, { status: 'error', message: first.message ?? 'The request failed' }) // FALLBACK-OK: SYS-56 (a failure with no server message shows a generic line)
      out.retries.set(agent, first.retry)
      continue
    }
    const rows: AnyRow[] = [...first.rows]
    for (let i = 1; i < count; i++) {
      const p = states.get(pageKey(agent, i * BAND_PAGE))
      if (p?.status !== 'ready') break
      rows.push(...p.rows)
    }
    out.pages.set(agent, { rows })
    out.bands.set(agent, { status: 'ready', total: first.total, loaded: rows.length })
  }
  return out
}
