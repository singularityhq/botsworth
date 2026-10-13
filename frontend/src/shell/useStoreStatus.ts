import { useEffect } from 'react'
import { client } from '../api/client'
import { useApi } from '../api/useApi'
import { bannerKind } from '../ui/banner.logic'

const POLL_MS = 60_000

/** FE-7, D38 H3: the status poll (every 60 s and when the window regains focus) feeds the freshness text and the banner only.
 *  Nothing here refetches or clears any data. */
export function useStoreStatus() {
  const { data, error, retry } = useApi('/api/ingest/status', (signal) => client.ingestStatus(signal))
  useEffect(() => {
    const id = setInterval(retry, POLL_MS)
    window.addEventListener('focus', retry)
    return () => {
      clearInterval(id)
      window.removeEventListener('focus', retry)
    }
  }, [retry])
  return { status: data, error, banner: bannerKind(data), retry }
}
