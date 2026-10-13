import { client, urls } from '../../api/client'
import type { FindResult } from '../../api/types'
import { useApi } from '../../api/useApi'
import { hitPool, matchingRows, type FinderKind, type Hit, type TurnPositions } from './finders'

export interface FinderSet {
  /** The hits the chip counts and the key walks (SES-51). undefined until the request returns (D38 M8) or while it has failed. */
  pool: Hit[] | undefined
  /** The request failed: the chip shows the D41 M8 state and a click retries. */
  failed: boolean
  retry: () => void
}

type Search = { active: boolean; result: FindResult | undefined }

function useFinder(session: string, kind: FinderKind, positions: TurnPositions | undefined, search: Search): FinderSet {
  const r = useApi<FindResult>(urls.sessionFind(session, { kind }), (signal) => client.sessionFind(session, { kind }, signal))
  const waiting = r.data === undefined || positions === undefined || (search.active && search.result === undefined)
  return {
    pool: waiting ? undefined : hitPool(r.data!, positions!, search.active ? matchingRows(search.result) : null),
    failed: r.error !== null,
    retry: r.retry,
  }
}

/**
 * One find request per kind (API-25), each a hit set the chip, the key and the toast all read (D38 M4). With a search active a set holds
 * only the rows that match it, so the chips count only those rows (SES-51); while that search is still loading the sets show no count.
 * `positions` is undefined until the turn list has loaded.
 */
export function useFinders(session: string, positions: TurnPositions | undefined, search: Search): Record<FinderKind, FinderSet> {
  return {
    waits: useFinder(session, 'waits', positions, search),
    failures: useFinder(session, 'failures', positions, search),
    skills: useFinder(session, 'skills', positions, search),
    agents: useFinder(session, 'agents', positions, search),
  }
}
