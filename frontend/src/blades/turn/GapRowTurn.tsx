import type { GapRow } from '../../api/types'
import { gapState, stateFill } from '../shared/states'
import { LedgerFrame, Glyph, When, TimeCell, type RowEnv } from './rowKit'
import { gapName } from './rowName'
import type { RowNode } from './rowList.logic'

/**
 * TRN-34: a stretch of 2 min or more in which nothing ran on the main thread. Glyph and label by kind, the duration (mono 500 ink1), the label
 * (ink2), the reason (ink3), a hatched bar (solid `--st-agents` when only agents ran) against the list's largest time, the duration in Time.
 * l2 fill; the cursor's struct-soft wins (the frame sets it on `data-open`).
 */
export function GapRowTurn({ node, env, firstTurnIsHuman, maxMs }: { node: RowNode; env: RowEnv; firstTurnIsHuman: boolean; maxMs: number }) {
  const g = node.row as GapRow
  const n = gapName(g, firstTurnIsHuman)
  const pct = maxMs > 0 ? Math.max(2, (100 * g.dur_ms) / maxMs) : 2
  return (
    <LedgerFrame
      env={env}
      node={node}
      className="bg-l2"
      title={`${n.dur} ${n.label}, ${n.reason}`}
      cells={{
        when: <When ms={g.start_ms} tz={env.tz} />,
        glyph: <Glyph name={n.glyph} />,
        name: (
          <>
            <b className="fig shrink-0 font-medium text-ink1">{n.dur}</b>
            <span className="shrink-0 text-ink2">{n.label}</span>
            <span className="min-w-0 truncate text-ink3">{n.reason}</span>
          </>
        ),
        bar: (
          <span aria-hidden="true" className="flex w-[104px] justify-end">
            <i className="block h-3 rounded-[2px]" style={{ width: `${pct}%`, ...stateFill(gapState(g.label)) }} />
          </span>
        ),
        time: <TimeCell ms={g.dur_ms} env={env} />,
      }}
    />
  )
}
