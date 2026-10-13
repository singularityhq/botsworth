import type { IngestStatus } from '../api/types'

export type BannerKind = 'no_store' | 'rebuild' | 'ingest'

/** SYS-57, SYS-57a, FE-8: which banner the status shows, if any. A missing store wins over a running ingest. */
export function bannerKind(status: IngestStatus | undefined): BannerKind | null {
  // FALLBACK-OK: FE-8 (before the first status response no banner shows)
  if (!status) return null
  if (status.store_state === 'missing' || !status.store_exists) return 'no_store'
  if (status.store_state === 'schema_mismatch') return 'rebuild'
  return status.ingest.running ? 'ingest' : null
}

export const BANNER_TEXT: Record<BannerKind, string> = {
  no_store: 'No Botsworth store yet.',
  rebuild: 'Store needs a rebuild.',
  ingest: 'Ingest running — figures may be partial',
}
