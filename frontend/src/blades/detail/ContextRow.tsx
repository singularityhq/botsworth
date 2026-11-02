import { Icon } from '../../ui/Icon'
import type { IconName } from '../../ui/glyphs'

/**
 * DET-5: a link to another row, 32 px: a leading figure or clock (mono ink3, or money), the row's glyph, its name (ink1, ellipsis) and an
 * optional trailing figure. Clicking it moves the turn's cursor to that row; the detail follows. A clickable row reads as a link: the name turns
 * struct and a chevron follows on hover and keyboard focus (the SYS-51 ring shows on focus).
 */
export function ContextRow({
  lead, glyph, name, trail, onClick, title, wide = false,
}: { /** The lead holds a clock range (HH:MM–HH:MM), which needs more room than a figure or a clock. */ wide?: boolean; lead?: React.ReactNode; glyph: IconName; name: string; trail?: React.ReactNode; onClick?: () => void; title?: string }) {
  const cls = `group/ctx grid min-h-8 w-full ${wide ? 'grid-cols-[104px_16px_minmax(0,1fr)_auto]' : 'grid-cols-[72px_16px_minmax(0,1fr)_auto]'} items-center gap-2 rounded-chip px-2 text-left text-ink1`
  const body = (
    <>
      <span className="fig truncate text-ink3">{lead}</span>
      <Icon name={glyph} tone="muted" />
      <span className="truncate transition-colors duration-150 group-hover/ctx:text-struct group-focus-visible/ctx:text-struct">{name}</span>
      <span className="flex items-center gap-2">
        {trail}
        {onClick ? <Icon name="chevron-right" tone="muted" className="transition-colors duration-150 group-hover/ctx:text-struct group-focus-visible/ctx:text-struct" /> : null}
      </span>
    </>
  )
  // FALLBACK-OK: DET-13 (a tool_use block with no row reference has nothing to go to: it is drawn as a plain line)
  if (!onClick) return <div className={cls} title={title ?? name}>{body}</div>
  return (
    <button type="button" title={title ?? name} onClick={onClick} className={`${cls} transition-colors duration-150 hover:bg-l3`}>
      {body}
    </button>
  )
}
