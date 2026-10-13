/** SYS-39: linear from 0 to the visible list's maximum; a bar under 1 px is not drawn. Returns px, or 0 for none. */
export function barWidth(value: number | null, max: number, track: number): number {
  // FALLBACK-OK: SYS-39 (no figure, or an empty scale: no bar)
  if (value === null || !(max > 0) || !(value > 0)) return 0
  const w = Math.min(value / max, 1) * track
  // FALLBACK-OK: SYS-39 (a bar under 1 px is not drawn)
  return w < 1 ? 0 : w
}
