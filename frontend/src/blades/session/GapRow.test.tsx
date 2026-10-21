// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { buildEntries, listColumns, type ListEntry } from './turnList.logic'
import { GapRowView } from './GapRow'
import { gap, turn } from './testRows'

afterEach(cleanup)

const columns = listColumns(1400)

function gapEntry(o: Parameters<typeof gap>[2]): Extract<ListEntry, { type: 'gap' }> {
  const e = buildEntries([turn(1), gap(0, 600_000, { turn: 's/turn/1', ...o }), turn(2, { start_ms: 2_000_000, end_ms: 2_100_000 })]).find((x) => x.type === 'gap')
  if (!e || e.type !== 'gap') throw new Error('no gap entry')
  return e
}

function mount(entry: Extract<ListEntry, { type: 'gap' }>) {
  render(<GapRowView entry={entry} prevStartMs={null} maxMs={600_000} onOpen={() => {}} tz="UTC" singleDay columns={columns} />)
}

describe('gap rows of D55 (SES-19a)', () => {
  it('shows a none/stalled stretch as Stalled with its reason, counted under none', () => {
    const e = gapEntry({ state: 'none', substate: 'stalled' })
    expect(e.state).toBe('none')
    mount(e)
    expect(screen.getByText('Stalled')).toBeTruthy()
    expect(screen.getByText('a finished task waited for an agent that was not running')).toBeTruthy()
  })

  it('shows a tool/background stretch as Background command with its reason, counted under tool', () => {
    const e = gapEntry({ state: 'tool', substate: 'background' })
    expect(e.state).toBe('tool')
    mount(e)
    expect(screen.getByText('Background command')).toBeTruthy()
    expect(screen.getByText('a background command ran; nothing else did')).toBeTruthy()
  })
})
