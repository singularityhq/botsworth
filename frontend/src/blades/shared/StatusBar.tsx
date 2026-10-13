/** SES-70: the 36 px line along the bottom of the stage: hairline above, 24 px padding, ink2, one line, overflow hidden. Key hints are not shown here (SYS-50). */
export function StatusBar({ children }: { children: React.ReactNode }) {
  return (
    <div data-status-bar className="flex h-9 shrink-0 items-center gap-4 overflow-hidden whitespace-nowrap border-t border-hair px-6 text-ink2">
      {children}
    </div>
  )
}

/** The leading text of the bar: ink1 500. */
export function StatusPos({ children }: { children: React.ReactNode }) {
  return <span className="font-medium text-ink1">{children}</span>
}
