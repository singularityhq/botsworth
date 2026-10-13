/** SYS-54: l2 fill with a slow pulse (off under reduced motion). Never shows zeros or "Loading…". */
export function Skeleton({ width, height = 14, className = '' }: { width?: number | string; height?: number | string; className?: string }) {
  return <span aria-hidden="true" className={`skeleton inline-block align-middle ${className}`} style={{ width: width ?? '100%', height }} />
}

/** Rows of the row's height (SYS-22) while a list loads. */
export function SkeletonRows({ count = 8, rowHeight = 44 }: { count?: number; rowHeight?: number }) {
  return (
    <div role="status" aria-label="Loading">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center border-b border-rule-soft px-4" style={{ height: rowHeight }}>
          <Skeleton height={14} width={`${40 + ((i * 17) % 45)}%`} />
        </div>
      ))}
    </div>
  )
}
