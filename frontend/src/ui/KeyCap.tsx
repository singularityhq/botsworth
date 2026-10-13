/** SYS-15: key caps are the one 12 px mono use besides chart numerals. l4 surface (SYS-1). */
export function KeyCap({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd className={`inline-flex min-w-5 items-center justify-center rounded-chip border border-rule bg-l4 px-1.5 font-mono text-label text-ink3 ${className}`}>
      {children}
    </kbd>
  )
}
