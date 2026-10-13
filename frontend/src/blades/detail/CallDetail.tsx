import type { CallDetail as CallDetailWire, CallRow } from '../../api/types'
import { fmt } from '../../lib/format'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { ModelMark } from '../../ui/ModelMark'
import { Skeleton } from '../../ui/Skeleton'
import { FinalTag } from '../turn/CallRow'
import { ModelFlag } from '../turn/rowKit'
import { durText } from '../turn/rowName'
import {
  costCaption, planCall, speedNoteShown, SPEED_NOTE, speedTokS, thinkingState, tokenLine, ttftText, type CallPlan,
} from './detail.logic'
import { ContextRow } from './ContextRow'
import { Hero, HeroFigure, InfoLine, RowsSkeleton, Section, WellSkeleton } from './parts'
import { rowTitle, type DetailCtx } from './rowTitle'
import { SourceBody, SourceView } from './SourceBody'
import { TextWell } from './TextWell'
import { useSourceText } from './useSourceText'

type CallData = Omit<CallRow, 'type'>

export interface LoadError {
  message: string
  retry: () => void
}

export interface CallDetailProps {
  ctx: DetailCtx
  /** The call as the turn blade holds it: the hero renders from it at once (DET-70). The op detail's copy of the same fields replaces it when it arrives. */
  row: CallRow | undefined
  detail: CallDetailWire | undefined
  error: LoadError | null
  turnCost: number | null
}

/** A figure in the strip: icon, a figure in ink1 500, a label. */
export function Stat({ icon, figure, label }: { icon: 'timer' | 'coins' | 'arrow-left-right' | 'terminal'; figure: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon name={icon} tone="muted" />
      <b className="fig font-medium text-ink1">{figure}</b>
      {label}
    </span>
  )
}

/** DET-10 to DET-14, DET-61 to DET-64: a model call. */
export function CallDetailView({ ctx, row, detail, error, turnCost }: CallDetailProps) {
  const c: CallData | undefined = detail ?? row
  if (c === undefined) return <HeroSkeleton />
  const cost = c.cost_usd
  const caption = costCaption(cost, turnCost ?? detail?.turn_cost_usd ?? null) // FALLBACK-OK: DET-10 (the turn's cost comes from the turn's rows, else from the op detail; none known: no caption)
  const speed = speedTokS({ speed_tok_s: detail?.speed_tok_s ?? null, tok: c.tok, dur_ms: c.dur_ms }) // FALLBACK-OK: DET-11 (the op detail's speed arrives later; until then the row's own figures decide)
  const tokens = tokenLine(c.tok)
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Hero>
          {cost === null ? (
            <span className="text-lead font-semibold text-ink3">No price</span>
          ) : (
            <HeroFigure ink="text-money">
              {fmt.money(cost)}
              {c.cost_lower_bound ? <span className="text-ink3">+</span> : null}
            </HeroFigure>
          )}
          {caption ? <span className="text-ink3">{caption}</span> : null}
        </Hero>
        {cost === null ? (
          <InfoLine>{c.model ? `No price for ${c.model}. This call is not in the cost totals.` : 'No price: the model is not recorded. This call is not in the cost totals.'}</InfoLine>
        ) : null}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-ink3">
          {c.model ? (
            <ModelMark model={c.model} />
          ) : (
            <span className="inline-flex items-center gap-1">
              <ModelFlag model={null} />
              unknown model
            </span>
          )}
          {c.dur_ms !== null ? <Stat icon="timer" figure={fmt.dur(c.dur_ms)} label="model time" /> : null}
          {speed !== null ? (
            <span>
              <b className="fig font-medium text-ink1">{speed}</b> tok/s out
            </span>
          ) : null}
          {c.ttft_ms !== null ? <span className="fig">{ttftText(c.ttft_ms)}</span> : null}
          {c.tok.total !== null ? <Stat icon="coins" figure={fmt.tokens(c.tok.total)} label="tokens" /> : null}
          {c.stop_reason ? <span>stop {c.stop_reason}</span> : null}
        </div>
        {speedNoteShown(c) ? <InfoLine>{SPEED_NOTE}</InfoLine> : null}
        {tokens.length > 0 ? (
          <p className="text-ink3">
            {tokens.map((t, i) => (
              <span key={t.label}>
                {i > 0 ? ' · ' : ''}
                {t.label} <span className="fig text-ink2">{fmt.tokens(t.value)}</span>
              </span>
            ))}
          </p>
        ) : null}
      </div>
      {error ? (
        <ErrorCard noun="details" message={error.message} onRetry={error.retry} />
      ) : detail === undefined ? (
        <PendingSections ctx={ctx} call={c} />
      ) : (
        <Sections ctx={ctx} detail={detail} />
      )}
    </div>
  )
}

function HeroSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton width={120} height={36} />
      <Skeleton width={360} height={16} />
    </div>
  )
}

/** DET-70: while the op detail loads, the section heads the row already tells us (its steps) with skeletons inside. */
function PendingSections({ ctx, call }: { ctx: DetailCtx; call: CallData }) {
  const tools = ctx.toolNames.get(call.id)?.length ?? 0 // FALLBACK-OK: DET-70 (tool names come from the loaded rows; unknown: no tool section until the detail arrives)
  return (
    <>
      {call.steps.map((s) =>
        s.verb === 'think' ? (
          <Section key={s.step_id} icon="brain" title="Thinking">
            <WellSkeleton />
          </Section>
        ) : (
          <Section key={s.step_id} icon="message-square" title={s.verb === 'answer' ? 'Final answer' : 'Said'}>
            <WellSkeleton />
          </Section>
        ),
      )}
      {tools > 0 ? (
        <Section icon="terminal" title="Tool calls" count={tools}>
          <RowsSkeleton rows={Math.min(tools, 3)} />
        </Section>
      ) : null}
    </>
  )
}

function Sections({ ctx, detail }: { ctx: DetailCtx; detail: CallDetailWire }) {
  const plan = planCall(detail)
  const tools = plan.filter((p) => p.part === 'tool_use')
  const firstTool = plan.findIndex((p) => p.part === 'tool_use')
  return (
    <>
      {plan.map((p, i) => {
        if (p.part === 'thinking') return <ThinkingSection key={`${i}-${p.section.src}`} ctx={ctx} plan={p} reasoningTok={detail.tok.reasoning} />
        if (p.part === 'text') return <SaidSection key={`${i}-${p.section.src}`} ctx={ctx} plan={p} />
        if (i !== firstTool) return null
        return <ToolCallsSection key="tools" ctx={ctx} plans={tools} />
      })}
      {plan.length === 0 ? <InfoLine>No text recorded for this model call.</InfoLine> : null}
    </>
  )
}

/** DET-13, DET-63, DET-64: thinking; the Codex reasoning summary and its encrypted form; empty and redacted. */
function ThinkingSection({ ctx, plan, reasoningTok }: { ctx: DetailCtx; plan: CallPlan; reasoningTok: number | null }) {
  const state = useSourceText(ctx.session, plan.section.src, 'thinking')
  const kind = state.status === 'ready' ? thinkingState(state.data.block, state.data.text) : null
  const title = kind?.kind === 'text' ? kind.heading : 'Thinking'
  return (
    <Section icon="brain" title={title}>
      <SourceView state={state} src={plan.section.src}>
        {(d) => {
          const k = thinkingState(d.block, d.text)
          switch (k.kind) {
            case 'text':
              return <TextWell text={d.text} variant="prose" clamp={3} label={k.heading.toLowerCase()} />
            case 'encrypted':
              return (
                <div className="flex flex-col gap-1 text-ink3">
                  <p>Reasoning is encrypted in the Codex log.</p>
                  {reasoningTok !== null ? (
                    <p>
                      <span className="fig text-ink2">{fmt.tokens(reasoningTok)}</span> reasoning tokens
                    </p>
                  ) : null}
                </div>
              )
            case 'redacted':
              return <p className="text-ink3">Thinking redacted in the source.</p>
            case 'empty':
              return <p className="text-ink3">No thinking text in the source.</p>
          }
        }}
      </SourceView>
    </Section>
  )
}

/** DET-13: "Said", or "Final answer" with the `final` tag. */
function SaidSection({ ctx, plan }: { ctx: DetailCtx; plan: CallPlan }) {
  const final = plan.verb === 'answer'
  return (
    <Section icon="message-square" title={final ? 'Final answer' : 'Said'} aside={final ? <FinalTag /> : null}>
      <SourceBody session={ctx.session} src={plan.section.src} part="text">
        {(d) => <TextWell text={d.text} variant="prose" clamp={6} label={final ? 'final answer' : 'said text'} />}
      </SourceBody>
    </Section>
  )
}

/** DET-13, DET-5: one link row per tool row of the call: duration, glyph, name; an agent row adds its cost. */
function ToolCallsSection({ ctx, plans }: { ctx: DetailCtx; plans: CallPlan[] }) {
  return (
    <Section icon="terminal" title="Tool calls" count={plans.length}>
      <div className="-mx-2 flex flex-col">
        {plans.map((p, i) => {
          const ref = p.section.row
          const r = ref ? ctx.lookup(ref.id) : undefined
          if (r === undefined) {
            // FALLBACK-OK: DET-13 (a tool row outside the loaded rows is named by the stored snippet of its tool_use block)
            return <ContextRow key={ref?.id ?? i} glyph="terminal" name={p.section.snippet || 'Tool call'} onClick={ref ? () => ctx.goTo(ref.id) : undefined} />
          }
          const t = rowTitle(r, ctx)
          return (
            <ContextRow
              key={r.id}
              lead={r.type === 'tool' ? durText(r.dur_ms, r.time_prov) : undefined}
              glyph={t.glyph}
              name={t.text}
              trail={r.type === 'agent' && r.cost_usd !== null ? <span className="fig text-money">{fmt.money(r.cost_usd)}</span> : null}
              onClick={() => ctx.goTo(r.id)}
            />
          )
        })}
      </div>
    </Section>
  )
}
