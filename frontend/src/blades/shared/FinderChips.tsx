import { Icon } from '../../ui/Icon'
import { KeyCap } from '../../ui/KeyCap'
import { Tooltip } from '../../ui/Tooltip'
import { FINDERS, FINDER_KINDS, type FinderKind } from './finders'
import type { FinderSet } from './useFinders'

interface ChipProps {
  icon: React.ComponentProps<typeof Icon>['name']
  count: number | null
  word: string
  keyCap: string
  tooltip: string
  /** SYS-8: the glyph and the word in error red (the failed-tool chip). */
  error?: boolean
  onClick: () => void
}

/** SES-31: `icon + mono count + word + key cap`, 32 px, ink2, hover l3. Clicking is the same as pressing the key. */
function Chip({ icon, count, word, keyCap, tooltip, error = false, onClick }: ChipProps) {
  return (
    <Tooltip content={tooltip}>
      <button
        type="button"
        onClick={onClick}
        className="inline-flex h-8 items-center gap-1.5 rounded-control px-2 text-ink2 transition-colors duration-150 ease-[var(--ease-standard)] hover:bg-l3 hover:text-struct"
      >
        <Icon name={icon} tone={error ? 'error' : 'quiet'} />
        {count !== null ? <b className="fig font-medium text-ink1">{count}</b> : null}
        <span className={error ? 'text-error' : undefined}>{word}</span>
        <KeyCap>{keyCap}</KeyCap>
      </button>
    </Tooltip>
  )
}

interface FinderChipsProps {
  sets: Record<FinderKind, FinderSet>
  onFind: (kind: FinderKind) => void
  /** The session blade passes it and gets the Biggest chip; the turn blade has none (TRN-10). */
  onBiggest?: () => void
  /** The turn blade passes its turn: the counts are then that turn's hits, not the session's (TRN-10). */
  turn?: string
}

/** The "Biggest" chip (session blade only) and the four finder chips (SES-31, TRN-10). Each finder chip is hidden at count 0, shows no count until its request returns, and shows the D41 M8 state when it failed. */
export function FinderChips({ sets, onFind, onBiggest, turn }: FinderChipsProps) {
  return (
    <>
      {onBiggest ? <Chip icon="crosshair" count={null} word="Biggest" keyCap="b" tooltip="Next biggest turn (b)" onClick={onBiggest} /> : null}
      {FINDER_KINDS.map((kind) => {
        const info = FINDERS[kind]
        const set = sets[kind]
        if (set.failed) {
          // D41 M8: circle-alert in ink3, no count, a click retries.
          return (
            <Tooltip key={kind} content="Couldn't load. Click to retry">
              <button
                type="button"
                onClick={set.retry}
                className="inline-flex h-8 items-center gap-1.5 rounded-control px-2 text-ink2 transition-colors duration-150 hover:bg-l3 hover:text-struct"
              >
                <Icon name="circle-alert" tone="muted" />
                <span>{info.many}</span>
                <KeyCap>{info.key}</KeyCap>
              </button>
            </Tooltip>
          )
        }
        const count = set.pool === undefined ? null : turn === undefined ? set.pool.length : set.pool.filter((h) => h.turn === turn).length
        if (count === 0) return null
        return (
          <Chip
            key={kind}
            icon={info.icon}
            count={count}
            word={count === 1 ? info.one : info.many}
            keyCap={info.key}
            tooltip={`Next ${info.what} (${info.key}${info.prevKey ? `, ${info.prevKey} for previous` : ''})`}
            error={kind === 'failures'}
            onClick={() => onFind(kind)}
          />
        )
      })}
    </>
  )
}
