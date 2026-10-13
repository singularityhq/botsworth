import type { IngestStatus } from '../api/types'
import { Brandmark } from '../ui/Brandmark'
import { Icon } from '../ui/Icon'
import { KeyCap } from '../ui/KeyCap'
import { Tooltip } from '../ui/Tooltip'
import { Breadcrumb } from './Breadcrumb'
import type { CrumbSpec } from './breadcrumb.logic'
import { Freshness } from './Freshness'

/** EXP-6: the top bar spans the stage above all blades. Left: logo and "Botsworth" (not a control), then the breadcrumb.
 *  Right: the Cmd+K button and the freshness text. There is no text input here. */
export function TopBar({
  blades, onCloseTo, onPalette, status, error,
}: { blades: readonly CrumbSpec[]; onCloseTo: (level: number) => void; onPalette: () => void; status: IngestStatus | undefined; error: unknown }) {
  return (
    <header className="flex h-[52px] shrink-0 items-center gap-5 border-b border-rule-soft bg-l1 px-5">
      <div className="flex shrink-0 items-center gap-2.5 border-r border-rule-soft pr-5">
        <Brandmark size={24} />
        <span className="text-body font-semibold text-ink2">Botsworth</span>
      </div>
      <div className="min-w-0 flex-1">
        <Breadcrumb blades={blades} onCloseTo={onCloseTo} />
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <Tooltip content="Jump to a session, date or filter" side="bottom">
          <button
            type="button"
            aria-label="Open the command palette"
            onClick={onPalette}
            className="inline-flex h-7 items-center gap-2 rounded-control border border-edge bg-l1 pr-1 pl-2 text-ink2 transition-colors duration-150 hover:border-struct-line hover:bg-l2 hover:text-ink1"
          >
            <Icon name="command" tone="inherit" />
            <KeyCap>⌘K</KeyCap>
          </button>
        </Tooltip>
        <Freshness status={status} error={error} />
      </div>
    </header>
  )
}
