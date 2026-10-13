/** EXP-8: "Ingested 12 min ago". A null time reads "Never ingested". */
export function freshnessText(lastIngestMs: number | null, nowMs: number): string {
  if (lastIngestMs === null) return 'Never ingested'
  const min = Math.floor(Math.max(0, nowMs - lastIngestMs) / 60_000)
  if (min < 1) return 'Ingested just now'
  if (min < 60) return `Ingested ${min} min ago`
  const h = Math.floor(min / 60)
  if (h < 48) return `Ingested ${h} h ago`
  return `Ingested ${Math.floor(h / 24)} d ago`
}
