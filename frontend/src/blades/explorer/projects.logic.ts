import type { ColorSlot, PeriodSummary } from '../../api/types'
import type { Measure } from '../../state/url'
import { foldRows } from '../../ui/fold.logic'

/** Project panel rules (EXP-42 to EXP-46): ranking by the selected measure, the donut geometry, the legend fold and the click semantics. */

export type PanelProject = PeriodSummary['projects'][number]

export const TOP_PROJECTS = 6
export const DONUT = { size: 168, outer: 80, inner: 52 } as const

export function projectValue(p: PanelProject, measure: Measure): number {
  // FALLBACK-OK: explorer.md EXP-43 (a project with no priced or counted value ranks by 0)
  return (measure === 'cost' ? p.cost_usd : p.tok_total) ?? 0
}

/** Highest value first; equal values keep alphabetical order so the legend does not shuffle. */
export function rankProjects(projects: readonly PanelProject[], measure: Measure): PanelProject[] {
  return [...projects].sort((a, b) => projectValue(b, measure) - projectValue(a, measure) || a.project_name.localeCompare(b.project_name))
}

export interface Slice {
  /** The project key, or null for the shared "other" slice. */
  project: string | null
  name: string
  slot: ColorSlot | null
  value: number
  /** Number of projects the slice stands for. */
  count: number
  /** Radians from 12 o'clock, clockwise. */
  start: number
  end: number
}

export interface PanelModel {
  ranked: PanelProject[]
  total: number
  slices: Slice[]
  top: PanelProject[]
  rest: PanelProject[]
}

/** The top 6 get a slice each; every other project shares one slice (EXP-44). Zero-value projects get no slice. */
export function panelModel(projects: readonly PanelProject[], measure: Measure): PanelModel {
  const ranked = rankProjects(projects, measure)
  const total = ranked.reduce((a, p) => a + projectValue(p, measure), 0)
  const top = ranked.slice(0, TOP_PROJECTS)
  const rest = ranked.slice(TOP_PROJECTS)
  const parts: Omit<Slice, 'start' | 'end'>[] = top.map((p) => ({
    project: p.project, name: p.project_name, slot: p.color_slot, value: projectValue(p, measure), count: 1,
  }))
  const restValue = rest.reduce((a, p) => a + projectValue(p, measure), 0)
  if (rest.length > 0) parts.push({ project: null, name: `${rest.length} more`, slot: null, value: restValue, count: rest.length })
  let angle = 0
  const slices: Slice[] = []
  for (const part of parts) {
    if (part.value <= 0 || total <= 0) continue
    const sweep = (part.value / total) * Math.PI * 2
    slices.push({ ...part, start: angle, end: angle + sweep })
    angle += sweep
  }
  return { ranked, total, slices, top, rest }
}

/** The legend's fold: top 6 rows, then `Show N more · $X` with the tail's sum (EXP-45, SYS-38). */
export function legendFold(model: PanelModel, open: boolean, measure: Measure) {
  return foldRows(model.ranked, TOP_PROJECTS, open, (p) => projectValue(p, measure))
}

/** SVG path of one ring slice. A full circle is drawn as two half arcs (one arc cannot close on itself). */
export function slicePath(start: number, end: number, outer: number = DONUT.outer, inner: number = DONUT.inner, c: number = DONUT.size / 2): string {
  const pt = (r: number, a: number) => `${(c + r * Math.sin(a)).toFixed(2)} ${(c - r * Math.cos(a)).toFixed(2)}`
  if (end - start >= Math.PI * 2 - 1e-6) {
    return `M${pt(outer, 0)}A${outer} ${outer} 0 1 1 ${pt(outer, Math.PI)}A${outer} ${outer} 0 1 1 ${pt(outer, 0)}M${pt(inner, 0)}A${inner} ${inner} 0 1 0 ${pt(inner, Math.PI)}A${inner} ${inner} 0 1 0 ${pt(inner, 0)}Z`
  }
  const large = end - start > Math.PI ? 1 : 0
  return `M${pt(outer, start)}A${outer} ${outer} 0 ${large} 1 ${pt(outer, end)}L${pt(inner, end)}A${inner} ${inner} 0 ${large} 0 ${pt(inner, start)}Z`
}

/** Share text for the 40 px column: `82%`, `<1%` (SYS-29 through fmt.pct). */
export function shareOf(value: number, total: number): number {
  return total > 0 ? value / total : 0
}

/** EXP-46 click semantics. A plain click makes the filter exactly that project, or clears it when it is the only one selected.
 *  Shift-click and Cmd/Ctrl-click add the project to the filter or remove it. */
export function nextProjectFilter(current: readonly string[], project: string, additive: boolean): string[] {
  const on = current.includes(project)
  if (additive) return on ? current.filter((p) => p !== project) : [...current, project]
  return on && current.length === 1 ? [] : [project]
}

export type RowState = 'rest' | 'selected' | 'unselected'

/** SYS-53: with a filter on, selected rows are struct-soft and the others switch to ink3. */
export function rowState(filter: readonly string[], project: string): RowState {
  if (filter.length === 0) return 'rest'
  return filter.includes(project) ? 'selected' : 'unselected'
}

/** The scope the panel is computed under: every filter except the project filter (EXP-43, QRY-8). */
export function panelScope<T extends { projects: string[] }>(scope: T): T {
  return scope.projects.length === 0 ? scope : { ...scope, projects: [] }
}
