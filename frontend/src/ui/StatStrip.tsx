import { Icon } from './Icon'
import type { IconName } from './glyphs'

export interface StatItem {
  icon: IconName
  figure: React.ReactNode
  word: string
}

/** SYS-42: one display figure (30/500 money, tabular), then one 14 px line of icon + mono figure + word items. The caller passes the
 *  items in the fixed order: tokens, active of wall, agents, skill loads, errors (errors only when above 0). `children` is the per-model line. */
export function StatStrip({ figure, items, children }: { figure: React.ReactNode; items: readonly StatItem[]; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="text-display font-medium">{figure}</div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-body">
        {items.map((it) => (
          <span key={it.word} className="inline-flex items-center gap-1.5">
            <Icon name={it.icon} tone="muted" />
            <span className="fig text-ink2">{it.figure}</span>
            <span className="text-ink3">{it.word}</span>
          </span>
        ))}
      </div>
      {children}
    </div>
  )
}
