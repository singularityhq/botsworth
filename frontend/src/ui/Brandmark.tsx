/**
 * Brandmark: three receding rounded rects (a depth-layer motif that doubles as a rising cost bar).
 * Engraved, not filled: on a near-black ground a drop shadow has nowhere to go, so relief comes from
 * a struct-tinted light edge up-left and a dark edge down-right. The lit side takes the accent because
 * it is the same lamp that lights the focused blade.
 *
 * `size` drives both axes. Do not scale it below about 22 px: the edge strokes are absolute in viewBox
 * units, so below that the light and shadow strokes overlap.
 */
const BARS = (
  <>
    <rect x="18" y="58" width="42" height="84" rx="11" />
    <rect x="72" y="37" width="42" height="126" rx="11" />
    <rect x="126" y="15" width="56" height="170" rx="14" />
  </>
)

export function Brandmark({ size = 26, className = '' }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 200 200" width={size} height={size} className={`block shrink-0 overflow-visible ${className}`} aria-hidden="true" focusable="false">
      <g className="fill-none stroke-ground" strokeWidth={9} transform="translate(5 5)">
        {BARS}
      </g>
      <g className="fill-none stroke-struct-line" strokeWidth={9} transform="translate(-4 -4)">
        {BARS}
      </g>
      <g className="fill-l1">{BARS}</g>
    </svg>
  )
}
