import { describe, expect, it } from 'vitest'
import type { ColorSlot } from '../../api/types'
import {
  legendFold, nextProjectFilter, panelModel, panelScope, projectValue, rankProjects, rowState, shareOf, slicePath, type PanelProject,
} from './projects.logic'

function proj(name: string, cost: number | null, tok: number | null = cost === null ? null : cost * 1000, slot: ColorSlot | null = null): PanelProject {
  return {
    project: `/p/${name}`, project_name: name, color_slot: slot, cost_usd: cost, cost_lower_bound: false, n_unpriced: 0, tok_total: tok, n_sessions: 1,
  }
}

/** 26 projects: cost 1000, 900, ... falling; tokens in the reverse order for the last ones to test the measure switch. */
const many = Array.from({ length: 26 }, (_, i) => proj(`p${String(i).padStart(2, '0')}`, 1000 - i * 30, i === 25 ? 9e12 : (26 - i) * 1000, i < 6 ? ((i + 1) as ColorSlot) : null))

describe('rankProjects (EXP-43)', () => {
  it('ranks by the selected measure, highest first', () => {
    expect(rankProjects(many, 'cost')[0].project_name).toBe('p00')
    expect(rankProjects(many, 'tokens')[0].project_name).toBe('p25')
  })
  it('breaks a tie by name so the legend does not shuffle', () => {
    expect(rankProjects([proj('b', 5), proj('a', 5)], 'cost').map((p) => p.project_name)).toEqual(['a', 'b'])
  })
  it('ranks a project with no value last', () => {
    expect(rankProjects([proj('n', null), proj('x', 1)], 'cost').map((p) => p.project_name)).toEqual(['x', 'n'])
    expect(projectValue(proj('n', null), 'cost')).toBe(0)
  })
})

describe('panelModel (EXP-44)', () => {
  const m = panelModel(many, 'cost')
  it('gives the top 6 a slice each and the other 20 one shared slice', () => {
    expect(m.slices).toHaveLength(7)
    expect(m.slices[6]).toMatchObject({ project: null, slot: null, count: 20 })
    expect(m.slices.slice(0, 6).map((s) => s.name)).toEqual(['p00', 'p01', 'p02', 'p03', 'p04', 'p05'])
  })
  it('keeps a top-6 project whose slot is "other" as its own grey slice', () => {
    const grey = panelModel([proj('a', 10, 1, null), proj('b', 5, 1, 1)], 'cost')
    expect(grey.slices[0]).toMatchObject({ project: '/p/a', slot: null })
  })
  it('starts at 12 o clock, runs clockwise in rank order and closes the circle', () => {
    expect(m.slices[0].start).toBe(0)
    for (let i = 1; i < m.slices.length; i++) expect(m.slices[i].start).toBeCloseTo(m.slices[i - 1].end, 10)
    expect(m.slices[6].end).toBeCloseTo(Math.PI * 2, 10)
  })
  it('sums the measure over every project for the centre figure', () => {
    expect(m.total).toBe(many.reduce((a, p) => a + (p.cost_usd ?? 0), 0))
  })
  it('draws no slice for a zero-value project and none at all for an empty panel', () => {
    expect(panelModel([proj('z', 0)], 'cost').slices).toEqual([])
    expect(panelModel([], 'cost').slices).toEqual([])
  })
  it('recomputes the ranking, slices and total for Tokens', () => {
    const t = panelModel(many, 'tokens')
    expect(t.slices[0].name).toBe('p25')
    expect(t.total).toBeGreaterThan(9e12)
  })
})

describe('slicePath', () => {
  it('starts at the top of the outer ring for a slice that begins at 0', () => {
    expect(slicePath(0, Math.PI / 2).startsWith('M84.00 4.00')).toBe(true)
  })
  it('sets the large-arc flag past a half turn', () => {
    expect(slicePath(0, Math.PI * 1.5)).toContain('A80 80 0 1 1')
    expect(slicePath(0, Math.PI / 2)).toContain('A80 80 0 0 1')
  })
  it('draws a single full-circle slice as two half arcs', () => {
    const d = slicePath(0, Math.PI * 2)
    expect(d.match(/A80 80/g)).toHaveLength(2)
  })
})

describe('legendFold (EXP-45)', () => {
  const m = panelModel(many, 'cost')
  it('shows 6 rows and "Show 20 more" with the sum of the other 20', () => {
    const f = legendFold(m, false, 'cost')
    expect(f.visible).toHaveLength(6)
    expect(f.label).toBe('Show 20 more')
    expect(f.sum).toBe(m.rest.reduce((a, p) => a + (p.cost_usd ?? 0), 0))
  })
  it('expands in place to all 26 rows and drops the sum', () => {
    const f = legendFold(m, true, 'cost')
    expect(f.visible).toHaveLength(26)
    expect(f.sum).toBeNull()
  })
  it('has no fold with 6 or fewer projects', () => {
    expect(legendFold(panelModel(many.slice(0, 5), 'cost'), false, 'cost').label).toBe('')
  })
})

describe('nextProjectFilter (EXP-46)', () => {
  it('makes a plain click the exact filter', () => {
    expect(nextProjectFilter([], 'a', false)).toEqual(['a'])
    expect(nextProjectFilter(['a', 'b'], 'c', false)).toEqual(['c'])
  })
  it('clears the filter when the only selected project is clicked', () => {
    expect(nextProjectFilter(['a'], 'a', false)).toEqual([])
  })
  it('narrows to the clicked project when it is one of several selected', () => {
    expect(nextProjectFilter(['a', 'b'], 'a', false)).toEqual(['a'])
  })
  it('adds with shift or cmd and removes an already selected project', () => {
    expect(nextProjectFilter(['a'], 'b', true)).toEqual(['a', 'b'])
    expect(nextProjectFilter(['a', 'b'], 'a', true)).toEqual(['b'])
  })
  it('lets the last additive removal empty the filter', () => {
    expect(nextProjectFilter(['a'], 'a', true)).toEqual([])
  })
})

describe('rowState and panelScope', () => {
  it('marks rows selected or unselected only while a filter is on (SYS-53)', () => {
    expect(rowState([], 'a')).toBe('rest')
    expect(rowState(['a'], 'a')).toBe('selected')
    expect(rowState(['a'], 'b')).toBe('unselected')
  })
  it('computes the panel under the scope without the project filter, other fields unchanged (EXP-43)', () => {
    const scope = { from_day: '2026-10-06', q: 'x', projects: ['a', 'b'], models: ['m'] }
    expect(panelScope(scope)).toEqual({ from_day: '2026-10-06', q: 'x', projects: [], models: ['m'] })
    const none = { projects: [] as string[] }
    expect(panelScope(none)).toBe(none)
  })
  it('gives a share of the total', () => {
    expect(shareOf(25, 100)).toBe(0.25)
    expect(shareOf(5, 0)).toBe(0)
  })
})
