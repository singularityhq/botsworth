import type { ToolRow as ToolWire } from '../../api/types'
import { ErrorFlag, Glyph, LedgerFrame, QuietFlag, TimeCell, When, type RowEnv } from './rowKit'
import { failureText, silenceText, toolName, WAITS_TEXT } from './rowName'
import type { RowNode } from './rowList.logic'

/**
 * TRN-32: a tool call. A glyph by tool, the step summary and the target in mono ink3, the failure flag, the silence and waits flags, the
 * duration (`≈` when derived). A skill load keeps its ink1 glyph and a 600 name with its sub-text. Cost and Tokens are blank (SYS-31).
 */
export function ToolRowView({ node, env }: { node: RowNode; env: RowEnv }) {
  const r = node.row as ToolWire
  const n = toolName(r)
  const failure = failureText(r)
  const silence = silenceText(r)
  const wide = env.grid.ids.includes('bar')
  const textFlags = (
    <>
      {silence ? <QuietFlag text={silence} /> : null}
      {r.waits_on_user ? <QuietFlag text={WAITS_TEXT} /> : null}
      {failure && !wide ? <ErrorFlag text={failure} /> : null}
    </>
  )
  return (
    <LedgerFrame
      env={env}
      node={node}
      title={n.target ? `${n.text} ${n.target}` : n.text}
      cells={{
        when: <When ms={r.start_ms} tz={env.tz} />,
        glyph: <Glyph name={n.glyph} tone={n.skill ? 'inherit' : 'quiet'} />,
        name: (
          <>
            <span className={`min-w-0 shrink-[0.1] truncate group-data-[open=true]/row:text-struct ${n.skill ? 'font-semibold text-ink1' : 'text-ink1'}`}>{n.text}</span>
            {n.sub ? <span className="shrink-0 text-ink3">{n.sub}</span> : null}
            {n.target ? <span className="fig min-w-0 shrink-[4] truncate text-ink3">{n.target}</span> : null}
            {textFlags}
          </>
        ),
        flags: failure && wide ? <ErrorFlag text={failure} /> : null,
        time: <TimeCell ms={r.dur_ms} env={env} prov={r.time_prov} />,
      }}
    />
  )
}
