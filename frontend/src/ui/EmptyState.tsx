import { Icon } from './Icon'
import type { IconName } from './glyphs'

/** SYS-55: one 14 px ink2 sentence, a 24 px ink3 glyph and a struct action when one exists, centred in the region. */
export function EmptyState({ icon, children, action }: { icon?: IconName; children: React.ReactNode; action?: { label: string; onClick: () => void } }) {
  return (
    <div className="flex h-full min-h-32 flex-col items-center justify-center gap-2 p-6 text-center">
      {icon ? <Icon name={icon} size={24} tone="muted" /> : null}
      <p className="text-body text-ink2">{children}</p>
      {action ? (
        <button type="button" onClick={action.onClick} className="text-body text-struct hover:underline">
          {action.label}
        </button>
      ) : null}
    </div>
  )
}
