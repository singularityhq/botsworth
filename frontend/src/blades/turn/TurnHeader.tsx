import type { SessionDetail, TurnRow, TurnRows } from '../../api/types'
import { fmt } from '../../lib/format'
import type { MsRange } from '../../state/url'
import { Tokens } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import { Answers, type AnswersCost } from '../shared/Answers'
import { costFromRow, ZOOM_COST_NOTE } from './turn.logic'
import { ModelTable } from '../shared/ModelTable'
import { kindOf } from '../shared/states'
import type { DisplayKey } from '../shared/answers.logic'

type Turn = TurnRows['turn']

function Disclosure({ open, onClick, children }: { open: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1 rounded-control px-1.5 transition-colors duration-150 hover:text-struct ${open ? 'text-ink1' : 'text-ink3'}`}
    >
      <Icon name="chevron-right" tone="inherit" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
      {children}
    </button>
  )
}

/** TRN-2: a 28×28 icon button. A disabled one is drawn in the edge colour (a graphic, not dimmed text). */
function IconButton({ label, tip, icon, disabled = false, onClick }: { label: string; tip: string; icon: 'chevron-left' | 'chevron-right' | 'x'; disabled?: boolean; onClick: () => void }) {
  return (
    <Tooltip content={tip}>
      <button
        type="button"
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
        className="inline-flex h-7 w-7 items-center justify-center rounded-control text-ink3 transition-colors duration-150 hover:bg-l3 hover:text-struct disabled:cursor-default disabled:text-edge disabled:hover:bg-transparent disabled:hover:text-edge"
      >
        <Icon name={icon} tone="inherit" />
      </button>
    </Tooltip>
  )
}

const KIND_TINT = { message: 'text-type-user', slash: 'text-type-user', resume: 'text-type-user', agent: 'text-type-team', plugin: 'text-type-sys', scripted: 'text-type-sys', harness: '' } as const

export interface TurnHeaderProps {
  tz: string
  /** The turn's number, and how many numbered turns the session has. */
  ix: number
  total: number
  /** The session's turn row: shown at once (TRN-60). */
  row: TurnRow | undefined
  /** The turn's own rows response; undefined while loading. */
  turn: Turn | undefined
  /** The zoom and its clock-rule figures (TRN-24); `range` is undefined while they load. */
  zoom: MsRange | null
  range: TurnRows['range'] | undefined
  isFirst: boolean
  isLast: boolean
  overTimeToggle: boolean
  overTimeOpen: boolean
  info: boolean
  onPrev: () => void
  onNext: () => void
  onClose: () => void
  onToggleOver: () => void
  onToggleInfo: () => void
  onClearZoom: () => void
  onOpenMessage: () => void
  onJump: ((state: DisplayKey) => void) | undefined
  /** Replaces the answers when the rows request failed (TRN-61). */
  answersError?: React.ReactNode
}

/** TRN-2, TRN-3, TRN-4: the meta line, the title and the two answers. Padding 16 px 24 px 12 px. */
export function TurnHeader(p: TurnHeaderProps) {
  const r = p.row
  const kind = r ? kindOf(r.kind, r.origin, r.label) : null
  const cost: AnswersCost | undefined = p.zoom
    ? p.range
      ? { ...p.range.cost, main: p.range.cost_main, agents: p.range.cost_agents }
      : undefined
    : p.turn
      ? { ...p.turn.cost }
      : r
        ? costFromRow(r)
        : undefined
  const split = p.zoom ? p.range?.time_split : p.turn?.time_split ?? undefined // FALLBACK-OK: SES-85 (a turn with no start has no time split)
  const wall = p.zoom ? p.zoom.end_ms - p.zoom.start_ms : (p.turn ? (p.turn.wall_ms ?? 0) : undefined) // FALLBACK-OK: SES-85 (a turn with no span shows a wall of zero)
  return (
    <div className="shrink-0 px-6 pb-3 pt-4">
      <div className="flex min-h-7 items-center gap-2 text-ink3">
        {kind ? <Icon name={kind.icon} tone="inherit" label={kind.label} className={r ? KIND_TINT[kind.key] : ''} /> : null}
        <span className="text-ink2">
          Turn {p.ix} of {p.total}
        </span>
        {kind ? <span>{kind.label}</span> : null}
        {r && r.start_ms !== null && r.end_ms !== null ? <span className="fig">{fmt.range(r.start_ms, r.end_ms, p.tz)}</span> : null}
        {p.zoom ? (
          <span className="ml-2 inline-flex items-center gap-1.5 rounded-control bg-struct-soft py-0.5 pl-2 pr-1 text-struct">
            <Icon name="clock" tone="struct" />
            <span className="fig">
              {fmt.clock(p.zoom.start_ms, p.tz)}–{fmt.clock(p.zoom.end_ms, p.tz)}
            </span>
            <Tooltip content="Whole turn (0)">
              <button type="button" aria-label="Whole turn" onClick={p.onClearZoom} className="inline-flex rounded-chip p-0.5 hover:bg-l3">
                <Icon name="x" tone="struct" />
              </button>
            </Tooltip>
          </span>
        ) : null}
        <span className="flex-1" />
        {p.overTimeToggle ? (
          <Disclosure open={p.overTimeOpen} onClick={p.onToggleOver}>
            Over time
          </Disclosure>
        ) : null}
        <Disclosure open={p.info} onClick={p.onToggleInfo}>
          Details
        </Disclosure>
        <IconButton label="Previous turn" tip="Previous turn (T)" icon="chevron-left" disabled={p.isFirst} onClick={p.onPrev} />
        <IconButton label="Next turn" tip="Next turn (t)" icon="chevron-right" disabled={p.isLast} onClick={p.onNext} />
        <IconButton label="Close" tip="Close (Esc)" icon="x" onClick={p.onClose} />
      </div>
      <h1 className="mb-3 mt-0.5 min-w-0 text-lead font-semibold text-ink1">
        {r ? (
          <button type="button" onClick={p.onOpenMessage} title={r.label} className="block max-w-full truncate text-left hover:text-struct">
            {r.label}
          </button>
        ) : (
          <Skeleton width={360} height={22} />
        )}
      </h1>
      {p.answersError ?? (
        <Answers
          cost={cost}
          causal={!p.zoom}
          costNote={p.zoom ? ZOOM_COST_NOTE : undefined}
          wallMs={wall}
          split={split}
          onJump={p.onJump}
          className="[grid-template-columns:minmax(180px,1fr)_minmax(0,2fr)]! gap-x-8!"
        />
      )}
    </div>
  )
}

/** TRN-5: the stat line, the model table and speed note, and "Full message" with its length and an Open link. */
export function TurnDetails({ turn, provider, onOpenMessage }: { turn: Turn; provider: SessionDetail['session']['provider']; onOpenMessage: () => void }) {
  return (
    <div className="flex shrink-0 flex-col gap-3 border-b border-hair px-6 pb-4 pt-3" aria-label="Details">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-ink3">
        <span className="inline-flex items-center gap-1.5">
          <Icon name="coins" />
          {turn.tok_total === null ? <span className="fig font-medium text-ink1">—</span> : <Tokens value={turn.tok_total} ink="ink1" className="font-medium" />}
          tokens
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Icon name="arrow-left-right" />
          <span className="fig font-medium text-ink1">{turn.n_llm_calls}</span>
          model calls
        </span>
      </div>
      <ModelTable models={turn.models_full} provider={provider} />
      <div className="flex items-center gap-2">
        <Icon name="message-square-text" tone="quiet" />
        <b className="font-semibold text-ink1">Full message</b>
        {turn.text_bytes !== null ? <span className="text-ink3">{turn.text_bytes.toLocaleString('en-US')} characters</span> : null}
        <button type="button" onClick={onOpenMessage} className="inline-flex items-center gap-1 text-struct hover:underline">
          Open
          <Icon name="arrow-up-right" tone="struct" />
        </button>
      </div>
    </div>
  )
}
