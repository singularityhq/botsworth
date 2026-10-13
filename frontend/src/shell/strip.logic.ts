// SYS-46: strip widths and scroll targets. Pure maths; BladeStrip does the DOM.
export const STANDARD_W = 400
export const MIN_NEWEST_W = 640
export const GAP = 12
export const BLADE_MS = 280

/** Widths of n blades on a stage of width W. One blade takes W; otherwise parents are 400 and the newest takes the rest, at least 640. */
export function bladeWidths(n: number, stageW: number): number[] {
  if (n <= 0) return []
  if (n === 1) return [stageW]
  const newest = Math.max(MIN_NEWEST_W, stageW - STANDARD_W - GAP)
  return [...Array<number>(n - 1).fill(STANDARD_W), newest]
}

export function stripTotal(widths: readonly number[]): number {
  return widths.reduce((a, w) => a + w, 0) + GAP * Math.max(0, widths.length - 1)
}

/** Scroll position that shows the newest blade fully at the right edge. */
export function scrollToNewest(widths: readonly number[], stageW: number): number {
  return Math.max(0, stripTotal(widths) - stageW)
}

export function bladeLeft(widths: readonly number[], index: number): number {
  let x = 0
  for (let i = 0; i < index; i++) x += widths[i] + GAP
  return x
}

/** Scroll position that brings blade `index` fully into view with the least movement (SYS-47, focusMove). */
export function scrollToReveal(widths: readonly number[], index: number, scrollLeft: number, stageW: number): number {
  const left = bladeLeft(widths, index)
  const right = left + widths[index]
  if (left < scrollLeft) return left
  if (right > scrollLeft + stageW) return right - stageW
  return scrollLeft
}

/** SYS-50 / SES-5: move focus by delta, clamped to the open blades. */
export function moveFocus(count: number, current: number, delta: number): number {
  return Math.max(0, Math.min(count - 1, current + delta))
}

/** CSS cubic-bezier(x1, y1, x2, y2) as a function of progress 0 to 1. */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const cx = 3 * x1
  const bx = 3 * (x2 - x1) - cx
  const ax = 1 - cx - bx
  const cy = 3 * y1
  const by = 3 * (y2 - y1) - cy
  const ay = 1 - cy - by
  const sx = (t: number) => ((ax * t + bx) * t + cx) * t
  const sy = (t: number) => ((ay * t + by) * t + cy) * t
  const dx = (t: number) => (3 * ax * t + 2 * bx) * t + cx
  return (x: number) => {
    if (x <= 0) return 0
    if (x >= 1) return 1
    let t = x
    for (let i = 0; i < 8; i++) {
      const err = sx(t) - x
      if (Math.abs(err) < 1e-6) return sy(t)
      const d = dx(t)
      if (Math.abs(d) < 1e-6) break
      t -= err / d
    }
    let lo = 0
    let hi = 1
    t = x
    while (hi - lo > 1e-6) {
      if (sx(t) < x) lo = t
      else hi = t
      t = (lo + hi) / 2
    }
    return sy(t)
  }
}

/** SYS-58: the blade easing, cubic-bezier(.22, 1, .36, 1). */
export const easeBlade = cubicBezier(0.22, 1, 0.36, 1)
