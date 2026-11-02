import { Icon } from '../ui/Icon'
import { crumbList, truncateCrumb, type CrumbSpec } from './breadcrumb.logic'
import { useCrumbLabels } from './crumbs'

/** SYS-49: "Sessions" alone at blade 0 (14/500 ink1, no box). With blades open: Sessions › name › Turn N › detail. Ancestors are ink3
 *  buttons that close every blade to their right; the last crumb is ink1 500, never struct. */
export function Breadcrumb({ blades, onCloseTo }: { blades: readonly CrumbSpec[]; onCloseTo: (level: number) => void }) {
  const crumbs = crumbList(blades, useCrumbLabels())
  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-body">
      {crumbs.map((c, i) => (
        <span key={c.key} className="flex min-w-0 items-center gap-1.5">
          {i > 0 ? <Icon name="chevron-right" tone="muted" className="size-3.5" /> : null}
          {c.current ? (
            <span aria-current="page" title={c.text} className="truncate font-medium text-ink1">
              {truncateCrumb(c.text)}
            </span>
          ) : (
            <button
              type="button"
              title={c.text}
              onClick={() => onCloseTo(i)}
              className="hit-24 truncate text-ink3 transition-colors duration-150 hover:text-ink1 hover:underline focus-visible:text-struct"
            >
              {truncateCrumb(c.text)}
            </button>
          )}
        </span>
      ))}
    </nav>
  )
}
