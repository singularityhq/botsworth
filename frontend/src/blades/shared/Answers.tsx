import type { TimeSplit } from '../../api/types'
import { fmt } from '../../lib/format'
import { Duration } from '../../ui/figures'
import { Money } from '../../ui/figures'
import { Skeleton } from '../../ui/Skeleton'
import { Tooltip } from '../../ui/Tooltip'
import { costCaption, legendStates, runningSplit, stateTotals, youBreakdown } from './answers.logic'
import { SplitBar, type Segment } from './SplitBar'
import { STATE_KEYS, STATES, SUBSTATE_WORDING, stateFill, type StateKey } from './states'

export interface AnswersCost {
  cost_usd: number | null
  cost_lower_bound: boolean
  n_unpriced: number
  main: number | null
  agents: number | null
}

interface AnswersProps {
  cost: AnswersCost | undefined
  wallMs: number | undefined
  /** The DM-51 / DM-53 parts; the seven totals and the `you` substates come from them. */
  split: TimeSplit | undefined
  /** SES-13: false inside a brushed range, where the agents part counts calls that started in the range. */
  causal?: boolean
  /** A caveat shown as a tooltip on the cost figure (TRN-4, TRN-24: the zoomed cost does not equal the turn's total). */
  costNote?: string
  /** Legend click (SES-27). Without it the legend buttons are inert (the data to jump with has not arrived). */
  onJump?: (state: StateKey) => void
  className?: string
}

/** SES-26 (R3 top 5 #1): where the money went, where the time went. Shared by the session header and the turn header. */
export function Answers({ cost, wallMs, split, causal = true, costNote, onJump, className = '' }: AnswersProps) {
  return (
    <div className={`grid grid-cols-[minmax(220px,1fr)_minmax(0,2.2fr)] gap-x-10 ${className}`}>
      <CostAnswer cost={cost} causal={causal} note={costNote} />
      <TimeAnswer wallMs={wallMs} split={split} onJump={onJump} />
    </div>
  )
}

function CostAnswer({ cost, causal, note }: { cost: AnswersCost | undefined; causal: boolean; note?: string }) {
  if (!cost) {
    return (
      <div aria-busy="true">
        <Skeleton width={140} height={36} />
        <div className="my-2 h-2 rounded-[4px] bg-l2" />
        <Skeleton width={180} height={20} />
      </div>
    )
  }
  const c = costCaption(cost)
  const segments: Segment[] = [
    // FALLBACK-OK: SES-84 (an unknown part has no width; the split bar is drawn only when both parts are known)
    { key: 'main', value: c.main ?? 0, style: { background: 'var(--color-money)' }, title: `Main thread ${fmt.money(c.main)}` },
    {
      key: 'agents', value: c.agents ?? 0,
      style: { background: 'var(--color-money-bar)', boxShadow: 'inset 0 0 0 1px var(--color-money)' },
      title: `Agents ${fmt.money(c.agents)}`,
    },
  ]
  const figure =
    c.figure === null ? (
      <span className="fig text-display font-medium text-ink3">—</span>
    ) : (
      <Money value={c.figure} lowerBound={cost.cost_lower_bound} nUnpriced={cost.n_unpriced} className="text-display" />
    )
  return (
    <div>
      <div className="flex items-baseline">{note ? <Tooltip content={note}>{figure}</Tooltip> : figure}</div>
      {c.showSplit ? <SplitBar segments={segments} label="Cost split: main thread and agents" /> : <div className="h-3" />}
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-ink3">
        {c.noModelCalls ? (
          <span>no model calls</span>
        ) : (
          <>
            <span>
              main thread <Money value={c.main} weight={500} />
            </span>
            {c.showAgents ? (
              <Tooltip
                content={causal ? 'Whole cost of the agent runs this scope started or messaged, including work after it ended' : 'Agent model calls that started inside the range'}
              >
                <span>
                  {causal ? 'agents it started' : 'agent calls in range'} <Money value={c.agents} weight={500} />
                </span>
              </Tooltip>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}

function TimeAnswer({ wallMs, split, onJump }: { wallMs: number | undefined; split: TimeSplit | undefined; onJump?: (s: StateKey) => void }) {
  if (wallMs === undefined || !split) {
    return (
      <div aria-busy="true">
        <Skeleton width={220} height={36} />
        <div className="my-2 h-2 rounded-[4px] bg-l2" />
        <Skeleton width={320} height={20} />
      </div>
    )
  }
  const totals = stateTotals(split)
  const { running, nothing } = runningSplit(totals, wallMs)
  const segments: Segment[] = STATE_KEYS.map((k) => ({ key: k, value: totals[k], style: stateFill(k), title: `${STATES[k].label} ${fmt.dur(totals[k])}` }))
  return (
    <div>
      <div className="flex items-baseline gap-2.5 whitespace-nowrap">
        <span className="fig text-display font-medium text-ink1">{fmt.dur(wallMs)}</span>
        <span className="text-ink3">wall</span>
        <span className="ml-2 text-ink2">
          <b className="fig font-medium">{fmt.dur(running)}</b> running · <b className="fig font-medium">{fmt.dur(nothing)}</b> nothing running
        </span>
      </div>
      <SplitBar segments={segments} label="Time split over seven states" />
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {legendStates(totals).map((k) => (
          <Tooltip key={k} content={legendTip(k, split)}>
            <button
              type="button"
              disabled={!onJump}
              onClick={() => onJump?.(k)}
              className="inline-flex items-center gap-1.5 rounded-chip px-0.5 text-ink3 transition-colors duration-150 hover:text-struct disabled:cursor-default disabled:hover:text-ink3"
            >
              <i className="block h-2.5 w-2.5 shrink-0 rounded-[2px]" style={stateFill(k)} />
              {STATES[k].short}
              <Duration ms={totals[k]} ink="ink1" className="font-medium" />
            </button>
          </Tooltip>
        ))}
      </div>
    </div>
  )
}

/** SES-27 tooltip; SES-15: the `you` item lists its substates with their durations. */
function legendTip(k: StateKey, split: TimeSplit): React.ReactNode {
  const go = `Go to the longest stretch of: ${STATES[k].label}`
  if (k !== 'you') return go
  return (
    <>
      {go}
      {youBreakdown(split).map((r) => (
        <span key={r.substate} className="block">
          {SUBSTATE_WORDING[r.substate]} <span className="fig">{fmt.dur(r.ms)}</span>
        </span>
      ))}
    </>
  )
}
