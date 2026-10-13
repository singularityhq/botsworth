import { useCallback, useMemo, useState } from 'react'
import type { AnyRow } from '../../api/types'
import { AgentLoader } from './AgentBand'
import { assemblePages, pageKey, type PageState } from './agentPages'
import { BAND_PAGE, buildNodes, type BandState, type RowNode } from './rowList.logic'

const NONE: ReadonlySet<string> = new Set()

export interface AgentBands {
  /** Agent rows whose rows are shown (`l`, TRN-35). They persist across turn swaps. */
  open: ReadonlySet<string>
  /** The flat rows: the turn's own with every loaded agent page spliced in. */
  nodes: RowNode[]
  bands: ReadonlyMap<string, BandState>
  /** Render these once: they fetch the pages through `useApi`. */
  loaders: React.ReactNode
  /** Show or fold an agent's rows. Showing asks for its first page. */
  setOpen: (agent: string, on: boolean) => void
  /** Ask for an agent's first page without showing it (a finder is looking for a row inside). */
  ask: (agent: string) => void
  isAsked: (agent: string) => boolean
  /** "Show N more": the next page of an agent. */
  more: (agent: string) => void
  retry: (agent: string) => void
}

/**
 * TRN-35, D38 M8: which agents are open, which pages were asked for, and the rows they brought. The fetching is done by `AgentLoader`s, one
 * per agent page, so every request is a `useApi` request. `top` are the turn's rows (API-22).
 */
export function useAgentBands(top: readonly AnyRow[]): AgentBands {
  const [open, setOpenSet] = useState<ReadonlySet<string>>(NONE)
  const [asked, setAsked] = useState<ReadonlyMap<string, number>>(new Map())
  const [states, setStates] = useState<ReadonlyMap<string, PageState>>(new Map())

  const onPage = useCallback((agent: string, offset: number, s: PageState) => {
    setStates((m) => {
      const k = pageKey(agent, offset)
      const prev = m.get(k)
      if (prev && prev.status === s.status && prev.rows === s.rows && prev.total === s.total && prev.message === s.message && prev.retry === s.retry) return m
      return new Map(m).set(k, s)
    })
  }, [])

  const assembled = useMemo(() => assemblePages(states, asked), [states, asked])
  const nodes = useMemo(() => buildNodes(top, assembled.pages), [top, assembled.pages])

  const ask = useCallback((agent: string) => setAsked((m) => (m.has(agent) ? m : new Map(m).set(agent, 1))), [])
  const setOpen = useCallback(
    (agent: string, on: boolean) => {
      setOpenSet((s) => {
        if (s.has(agent) === on) return s
        const next = new Set(s)
        if (on) next.add(agent)
        else next.delete(agent)
        return next
      })
      if (on) ask(agent)
    },
    [ask],
  )
  const more = useCallback((agent: string) => setAsked((m) => new Map(m).set(agent, (m.get(agent) ?? 1) + 1)), []) // FALLBACK-OK: TRN-35 (an agent not asked yet has one page)
  const retry = useCallback((agent: string) => assembled.retries.get(agent)?.(), [assembled.retries]) // FALLBACK-OK: TRN-35 (only a failed first page has a retry; nothing to retry otherwise)

  const loaders = (
    <>
      {[...asked].flatMap(([agent, count]) =>
        Array.from({ length: count }, (_, i) => <AgentLoader key={pageKey(agent, i * BAND_PAGE)} agent={agent} offset={i * BAND_PAGE} onState={onPage} />),
      )}
    </>
  )
  return { open, nodes, bands: assembled.bands, loaders, setOpen, ask, isAsked: (a) => asked.has(a), more, retry }
}
