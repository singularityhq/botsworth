/** SYS-38: a list shows its top items and puts the rest one click away. */
export interface Fold<T> {
  visible: T[]
  hidden: T[]
  /** "Show 7 more" while folded, "Show top 5" once expanded. Empty when nothing is hidden. */
  label: string
  /** Sum of the hidden rows' primary figure while folded; null once expanded or when no row has one. */
  sum: number | null
}

export function foldRows<T>(items: readonly T[], top: number, open: boolean, value: (t: T) => number | null): Fold<T> {
  if (items.length <= top) return { visible: [...items], hidden: [], label: '', sum: null }
  const hidden = items.slice(top)
  if (open) return { visible: [...items], hidden, label: `Show top ${top}`, sum: null }
  let sum: number | null = null
  for (const h of hidden) {
    const v = value(h)
    if (v !== null) sum = (sum === null ? 0 : sum) + v
  }
  return { visible: items.slice(0, top), hidden, label: `Show ${hidden.length} more`, sum }
}
