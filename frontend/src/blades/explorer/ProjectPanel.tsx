import { useState } from 'react'
import type { PeriodSummary } from '../../api/types'
import { fmt } from '../../lib/format'
import type { Measure } from '../../state/url'
import { Money, Tokens } from '../../ui/figures'
import { FoldRow } from '../../ui/FoldRow'
import { Skeleton } from '../../ui/Skeleton'
import { ProjectDot } from '../../ui/ProjectDot'
import { seriesVar } from '../../ui/series'
import {
  DONUT, legendFold, nextProjectFilter, panelModel, projectValue, rowState, shareOf, slicePath, type PanelModel, type PanelProject,
} from './projects.logic'
import { slotOrder } from './dayChart.logic'

/** EXP-42 to EXP-46: the project count, the ring and the ranked legend. Every project, including the tail, filters the page. */

export interface ProjectPanelProps {
  projects: PeriodSummary['projects']
  measure: Measure
  /** The project filter in the URL. */
  selected: readonly string[]
  onFilter: (projects: string[]) => void
  /** The legend project under the pointer, as its chart slot order; undefined for none (EXP-41). */
  onHoverSlot: (slot: number | undefined) => void
}

/** `dim`: the row is unselected under a filter, so its figure switches to ink3 (SYS-53; text colour, not opacity). */
function Value({ p, measure, dim }: { p: Pick<PanelProject, 'cost_usd' | 'cost_lower_bound' | 'n_unpriced' | 'tok_total'>; measure: Measure; dim: boolean }) {
  return measure === 'cost' ? (
    <Money value={p.cost_usd} lowerBound={p.cost_lower_bound} nUnpriced={p.n_unpriced} weight={500} className={dim ? 'text-ink3!' : ''} />
  ) : (
    <Tokens value={p.tok_total} ink={dim ? 'ink3' : 'ink2'} />
  )
}

function LegendRow({ p, measure, total, selected, onClick, onHover }: { p: PanelProject; measure: Measure; total: number; selected: readonly string[]; onClick: (e: React.MouseEvent) => void; onHover: (slot: number | undefined) => void }) {
  const state = rowState(selected, p.project)
  const ink = state === 'unselected'
  return (
    <button
      type="button"
      aria-pressed={state === 'selected'}
      title={`${p.project_name} · ${p.n_sessions} ${p.n_sessions === 1 ? 'session' : 'sessions'} · click to filter, shift-click to add`}
      onClick={onClick}
      onPointerEnter={() => onHover(slotOrder(p.color_slot))}
      onPointerLeave={() => onHover(undefined)}
      onFocus={() => onHover(slotOrder(p.color_slot))}
      onBlur={() => onHover(undefined)}
      className={`grid h-7 w-full grid-cols-[10px_minmax(0,1fr)_auto_40px] items-center gap-2 rounded-control px-2 text-body transition-colors duration-150 ${
        state === 'selected' ? 'bg-struct-soft hover:bg-struct-soft' : 'hover:bg-l3'
      }`}
    >
      <ProjectDot slot={p.color_slot} size={10} />
      <span className={`truncate font-medium ${state === 'selected' ? 'text-struct' : ink ? 'text-ink3' : 'text-ink1'}`}>{p.project_name}</span>
      <span className="text-right">
        <Value p={p} measure={measure} dim={ink} />
      </span>
      <span className="fig text-right text-ink3">{fmt.pct(shareOf(projectValue(p, measure), total))}</span>
    </button>
  )
}

function Ring({ model, measure, selected, onSlice }: { model: PanelModel; measure: Measure; selected: readonly string[]; onSlice: (project: string | null, e: React.MouseEvent) => void }) {
  const c = DONUT.size / 2
  const dimmed = (project: string | null) =>
    selected.length > 0 && (project === null ? !model.rest.some((p) => selected.includes(p.project)) : !selected.includes(project))
  return (
    <svg viewBox={`0 0 ${DONUT.size} ${DONUT.size}`} width={DONUT.size} height={DONUT.size} role="img" aria-label="Share of each project" className="shrink-0">
      {model.slices.map((s) => (
        <path
          key={s.project ?? 'other'}
          d={slicePath(s.start, s.end)}
          fill={seriesVar(s.slot)}
          stroke="var(--color-l1)"
          strokeWidth={1.5}
          opacity={dimmed(s.project) ? 0.3 : 1}
          className="cursor-pointer hover:brightness-110"
          onClick={(e) => onSlice(s.project, e)}
        >
          <title>{`${s.name} · ${measure === 'cost' ? fmt.money(s.value) : fmt.tokens(s.value)} · ${fmt.pct(shareOf(s.value, model.total))}`}</title>
        </path>
      ))}
      <text x={c} y={c} textAnchor="middle" className={`font-mono text-body font-semibold ${measure === 'cost' ? 'fill-money' : 'fill-ink1'}`}>
        {measure === 'cost' ? fmt.money(model.total) : fmt.tokens(model.total)}
      </text>
      <text x={c} y={c + 20} textAnchor="middle" className="fill-ink3 text-body">
        {model.ranked.length} {model.ranked.length === 1 ? 'project' : 'projects'}
      </text>
    </svg>
  )
}

export function ProjectPanel({ projects, measure, selected, onFilter, onHoverSlot }: ProjectPanelProps) {
  const [open, setOpen] = useState(false)
  const model = panelModel(projects, measure)
  const fold = legendFold(model, open, measure)
  const click = (project: string, e: React.MouseEvent) => onFilter(nextProjectFilter(selected, project, e.shiftKey || e.metaKey || e.ctrlKey))
  const row = (p: PanelProject) => (
    <LegendRow key={p.project} p={p} measure={measure} total={model.total} selected={selected} onClick={(e) => click(p.project, e)} onHover={onHoverSlot} />
  )
  return (
    <section aria-label="Projects" className="min-w-0">
      <div className="mb-2 flex h-8 items-center justify-between">
        <h2 className="m-0 text-body font-semibold text-ink1">Projects</h2>
        <span className="text-body text-ink3">{model.ranked.length}</span>
      </div>
      <div className="grid grid-cols-[168px_minmax(0,1fr)] items-start gap-4">
        <Ring
          model={model}
          measure={measure}
          selected={selected}
          onSlice={(project, e) => (project === null ? setOpen(true) : click(project, e))}
        />
        <div className="min-w-0">
          {model.top.map(row)}
          {fold.label !== '' ? (
            <>
              <FoldRow
                label={fold.label}
                open={open}
                onToggle={() => setOpen((v) => !v)}
                sum={
                  fold.sum === null ? null : measure === 'cost' ? <Money value={fold.sum} weight={500} /> : <Tokens value={fold.sum} ink="ink2" />
                }
              />
              {open ? <div className="max-h-[196px] overflow-y-auto">{model.rest.map(row)}</div> : null}
            </>
          ) : null}
        </div>
      </div>
    </section>
  )
}

/** SYS-54: the ring and the legend rows are skeletons on the first load. */
export function ProjectPanelSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="grid grid-cols-[168px_minmax(0,1fr)] items-start gap-4">
      <span aria-hidden="true" className="skeleton inline-block size-[168px] rounded-full" />
      <div className="flex flex-col gap-1">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex h-7 items-center px-2">
            <Skeleton width={`${50 + ((i * 13) % 40)}%`} />
          </div>
        ))}
      </div>
    </div>
  )
}
