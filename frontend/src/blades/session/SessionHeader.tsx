import type { SessionDetail } from '../../api/types'
import { fmt } from '../../lib/format'
import type { MsRange } from '../../state/url'
import { Duration, Money, Tokens } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { ProjectDot } from '../../ui/ProjectDot'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import { Answers, type AnswersCost } from '../shared/Answers'
import { showUnattributed, stateTotals } from '../shared/answers.logic'
import { ModelTable } from '../shared/ModelTable'
import type { StateKey } from '../shared/states'

type Session = SessionDetail['session']

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

interface HeaderProps {
  session: Session | undefined
  tz: string
  over: boolean
  info: boolean
  range: MsRange | null
  onToggleOver: () => void
  onToggleInfo: () => void
  onClearRange: () => void
  /** SES-27; undefined until the turn list has loaded. */
  onJump: ((state: StateKey) => void) | undefined
  /** The keyboard button (SES-24): F3b fills this slot with the `?` overlay. */
  keysButton: React.ReactNode
  /** SES-13: with a range set, the cost answer under the clock rule. `null` while it loads; undefined with no range (the session's own cost). */
  rangeCost?: AnswersCost | null
}

/** SES-23 to SES-26: meta line, title, the two answers. Padding 16 px 24 px 12 px. */
export function SessionHeader({ session: s, tz, over, info, range, onToggleOver, onToggleInfo, onClearRange, onJump, keysButton, rangeCost }: HeaderProps) {
  return (
    <div className="shrink-0 px-6 pb-3 pt-4">
      <div className="flex min-h-7 items-center gap-2 text-ink3">
        {s ? (
          <>
            <ProjectDot slot={s.color_slot} />
            <span className="text-ink2">{s.project_name}</span>
            <span>{fmt.date(s.start_ms, tz)}</span>
            <span className="fig">{fmt.range(s.start_ms, s.end_ms, tz)}</span>
            <span>
              {s.n_turns} {s.n_turns === 1 ? 'turn' : 'turns'}
            </span>
            {range ? (
              <span className="ml-2 inline-flex items-center gap-1.5 rounded-control bg-struct-soft py-0.5 pl-2 pr-1 text-struct">
                <Icon name="clock" tone="struct" />
                <span className="fig">
                  {fmt.clock(range.start_ms, tz)}–{fmt.clock(range.end_ms, tz)}
                </span>
                <Tooltip content="All turns (0)">
                  <button type="button" aria-label="All turns" onClick={onClearRange} className="inline-flex rounded-chip p-0.5 hover:bg-l3">
                    <Icon name="x" tone="struct" />
                  </button>
                </Tooltip>
              </span>
            ) : null}
          </>
        ) : (
          <Skeleton width={320} />
        )}
        <span className="flex-1" />
        <Disclosure open={over} onClick={onToggleOver}>
          Over time
        </Disclosure>
        <Disclosure open={info} onClick={onToggleInfo}>
          Details
        </Disclosure>
        {keysButton}
      </div>
      <h1 className="mb-3 mt-0.5 truncate text-lead font-semibold text-ink1" title={s?.name}>
        {s ? s.title_source === 'fallback' ? (
          <span className="inline-flex items-center gap-1.5 font-mono text-body font-normal text-ink3">
            <Icon name="circle-dashed" />
            {s.name}
          </span>
        ) : (
          s.name
        ) : (
          <Skeleton width={360} height={22} />
        )}
      </h1>
      <Answers
        cost={rangeCost === undefined ? (s ? { ...s.cost } : undefined) : (rangeCost ?? undefined)} // FALLBACK-OK: SES-13 (null = the range figures are loading; the answer shows its skeleton)
        causal={rangeCost === undefined}
        wallMs={s?.wall_ms}
        totals={s ? stateTotals(s.time_split, s.none_split) : undefined}
        onJump={onJump}
      />
    </div>
  )
}

/** SES-28: the Details panel. A stat line, the agent work that has no turn, the model table and its note. */
export function DetailsPanel({ session: s }: { session: Session }) {
  return (
    <div className="flex shrink-0 flex-col gap-3 border-b border-hair px-6 pb-4 pt-3" aria-label="Details">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-ink3">
        <Stat icon="coins" figure={s.tok_total === null ? <span className="fig text-ink1">—</span> : <Tokens value={s.tok_total} ink="ink1" className="font-medium" />} word="tokens" />
        <Stat icon="arrow-left-right" figure={<span className="fig font-medium text-ink1">{s.n_llm_calls}</span>} word="model calls" />
        <Stat icon="activity" figure={<Duration ms={s.active_ms} ink="ink1" className="font-medium" />} word="active" />
        <Stat icon="bot" figure={<span className="fig font-medium text-ink1">{s.n_agents}</span>} word="agents" />
      </div>
      {showUnattributed(s.cost) ? (
        <p className="text-ink3">
          Agent work with no turn <Money value={s.cost.unattributed} weight={500} />
        </p>
      ) : null}
      <ModelTable models={s.models} provider={s.provider} />
    </div>
  )
}

function Stat({ icon, figure, word }: { icon: 'coins' | 'arrow-left-right' | 'activity' | 'bot'; figure: React.ReactNode; word: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon name={icon} />
      {figure}
      {word}
    </span>
  )
}
