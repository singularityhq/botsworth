import type { AgentDetail as AgentDetailWire, AgentRow } from '../../api/types'
import { fmt } from '../../lib/format'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { KeyCap } from '../../ui/KeyCap'
import { ModelMark } from '../../ui/ModelMark'
import { Stat, type LoadError } from './CallDetail'
import { ContextRow } from './ContextRow'
import { callsLedger, costCaption, runsText } from './detail.logic'
import { Hero, HeroFigure, InfoLine, RowsSkeleton, Section, WellSkeleton } from './parts'
import { rowTitle, type DetailCtx } from './rowTitle'
import { SourceBody } from './SourceBody'
import { TextWell } from './TextWell'

type AgentData = Omit<AgentRow, 'type' | 'runs'>

export interface AgentDetailProps {
  ctx: DetailCtx
  row: AgentRow | undefined
  detail: AgentDetailWire | undefined
  error: LoadError | null
  turnCost: number | null
  /** In a zoom: the agent's in-range cost and the range's cost (TRN-24, DET-30). */
  zoom: { agentCost: number | null; rangeCost: number | null } | null
  /** The agent's rows are shown in the turn (TRN-35). */
  rowsShown: boolean
  onToggleRows: () => void
}

/** DET-30 to DET-34: an agent run. */
export function AgentDetailView({ ctx, row, detail, error, turnCost, zoom, rowsShown, onToggleRows }: AgentDetailProps) {
  const a: AgentData | undefined = detail ?? row
  if (a === undefined) return null
  const cost = zoom ? zoom.agentCost : a.cost_usd
  const caption = zoom ? costCaption(cost, zoom.rangeCost, 'range') : costCaption(cost, turnCost ?? detail?.turn_cost_usd ?? null) // FALLBACK-OK: DET-30 (the turn's cost comes from the turn's rows, else from the op detail)
  const runs = runsText(detail?.runs.length ?? row?.runs.length ?? 0) // FALLBACK-OK: DET-31 (the runs of the row stand in until the op detail arrives)
  const models = [...new Set(a.models)].slice(0, 3)
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Hero>
          {cost === null ? (
            <HeroFigure ink="text-ink3">—</HeroFigure>
          ) : (
            <HeroFigure ink="text-money">
              {fmt.money(cost)}
              {a.cost_lower_bound ? <span className="text-ink3">+</span> : null}
            </HeroFigure>
          )}
          {caption ? <span className="text-ink3">{caption}</span> : null}
        </Hero>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-ink3">
          {models.map((m) => (
            <ModelMark key={m} model={m} />
          ))}
          <Stat icon="timer" figure={fmt.dur(a.run_ms)} label="ran" />
          {runs ? <span>{runs}</span> : null}
          <Stat icon="arrow-left-right" figure={String(a.n_llm_calls)} label="model calls" />
          <Stat icon="terminal" figure={String(a.n_tool_calls)} label="tool calls" />
          {a.n_errors > 0 ? (
            <span className="inline-flex items-center gap-1 text-error">
              <Icon name="circle-alert" tone="error" />
              {a.n_errors} {a.n_errors === 1 ? 'error' : 'errors'}
            </span>
          ) : null}
        </div>
        {a.n_rows > 0 || rowsShown ? (
          <div>
            <button type="button" onClick={onToggleRows} className="inline-flex h-7 items-center gap-2 rounded-control text-struct hover:underline">
              {rowsShown ? 'Fold its rows' : `Show its ${a.n_rows} ${a.n_rows === 1 ? 'row' : 'rows'} in the turn`}
              <KeyCap>{rowsShown ? 'h' : 'l'}</KeyCap>
            </button>
          </div>
        ) : null}
      </div>
      {error ? (
        <ErrorCard noun="details" message={error.message} onRetry={error.retry} />
      ) : detail === undefined ? (
        <>
          <Section icon={a.mode === 'messaged' ? 'send' : 'message-square-text'} title={a.mode === 'messaged' ? 'Message' : 'Asked'}>
            <WellSkeleton />
          </Section>
          <Section icon="arrow-left-right" title="Calls by cost">
            <RowsSkeleton rows={3} />
          </Section>
        </>
      ) : (
        <DetailSections ctx={ctx} a={a} detail={detail} />
      )}
    </div>
  )
}

function DetailSections({ ctx, a, detail }: { ctx: DetailCtx; a: AgentData; detail: AgentDetailWire }) {
  const focus = detail.runs.find((r) => r.engagement === detail.focus_engagement) ?? detail.runs[0] // FALLBACK-OK: DET-32 (the focus run, else the first run)
  const ask = focus?.ask_src ?? null // FALLBACK-OK: DET-69 (an engagement with no causing call has no ask text: the section is not rendered)
  const messaged = a.mode === 'messaged'
  const ledger = callsLedger(detail)
  return (
    <>
      {ask ? (
        <Section icon={messaged ? 'send' : 'message-square-text'} title={messaged ? 'Message' : 'Asked'}>
          <SourceBody session={ctx.session} src={ask} part="input">
            {(d) => <TextWell text={d.text} variant="prose" clamp={3} label={messaged ? 'message' : 'ask'} />}
          </SourceBody>
        </Section>
      ) : null}
      {!ask && ledger.top.length === 0 ? <InfoLine>No text recorded for this agent run.</InfoLine> : null}
      {ledger.top.length > 0 ? (
        <Section icon="arrow-left-right" title="Calls by cost" count={ledger.count}>
          <div className="-mx-2 flex flex-col">
            {ledger.top.map((c) => {
              const t = rowTitle(c, ctx)
              return (
                <ContextRow
                  key={c.id}
                  lead={c.cost_usd === null ? undefined : <span className="text-money">{fmt.money(c.cost_usd)}</span>}
                  glyph={t.glyph}
                  name={t.text}
                  onClick={() => ctx.goTo(c.id)}
                />
              )
            })}
          </div>
          {ledger.otherText ? <p className="text-ink3">{ledger.otherText}</p> : null}
        </Section>
      ) : null}
    </>
  )
}
