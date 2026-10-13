import { useEffect, useMemo } from 'react'
import { client, urls } from '../../api/client'
import type { AgentRow, AnyRow, CallRow, Detail, GapRow, NextTurn, ToolRow, TurnRows } from '../../api/types'
import { useApi } from '../../api/useApi'
import { useCrumb } from '../../shell/crumbs'
import { useBladeKeys, useEscape } from '../../shell/keys'
import { useNav } from '../../state/nav'
import { useUrlState } from '../../state/url'
import { ErrorCard } from '../../ui/ErrorCard'
import { Skeleton } from '../../ui/Skeleton'
import { kindOf, type KindInfo } from '../shared/states'
import { buildNodes, rowStart } from '../turn/rowList.logic'
import { clockSec } from '../turn/turn.logic'
import { AgentDetailView } from './AgentDetail'
import { CallDetailView, type LoadError } from './CallDetail'
import { DetailHeader } from './DetailHeader'
import { clip, stepKind } from './detail.logic'
import { GapDetailView } from './GapDetail'
import { MessageDetailView } from './MessageDetail'
import { Hero } from './parts'
import { clockRange, rowTitle, type DetailCtx } from './rowTitle'
import { ToolDetailView } from './ToolDetail'
import { useSourceText } from './useSourceText'
import { senderOf } from './detail.logic'
import { forgetLast, turnKnown, useTurnCursor, type TurnCursor } from './useTurnCursor'

/**
 * The detail blade (DET-1 to DET-70): one row of the turn in full, or the turn's own message. It follows the turn's cursor, the URL's `step`
 * (SES-9). The header and hero draw from the row the turn already holds; only the text wells and the op-detail sections load (DET-70).
 */
export function DetailBlade({ width, compact }: { width: number; compact: boolean }) {
  void width
  void compact
  const url = useUrlState()
  const nav = useNav()
  const known = turnKnown(url)
  // A detail with no turn behind it (`detail=1` without `t`) names nothing to show: the parameter is dropped (SES-11).
  useEffect(() => {
    if (known || !url.detail) return
    forgetLast()
    nav.replace({ detail: false })
  }, [known, url.detail, nav])
  return known ? <DetailBladeFor /> : null
}

function DetailBladeFor() {
  const c = useTurnCursor()
  const nav = useNav()
  const url = useUrlState()
  const kind = c.turnRow ? kindOf(c.turnRow.kind, c.turnRow.origin, c.turnRow.label) : null
  const step = c.step
  const what = step === null ? null : stepKind(step, c.turnOp)

  useBladeKeys('detail', (e) => {
    switch (e.key) {
      case 'j':
      case 'ArrowDown':
        c.move(1)
        return true
      case 'k':
      case 'ArrowUp':
        c.move(-1)
        return true
      case 'J':
        c.move(10)
        return true
      case 'K':
        c.move(-10)
        return true
      case 'l':
      case 'h': {
        const n = c.cursorNode
        if (n?.row.type !== 'agent') return false
        c.bands.setOpen(n.id, e.key === 'l')
        return true
      }
      case 'w':
        c.find('waits', 1)
        return true
      case 'W':
        c.find('waits', -1)
        return true
      case 'e':
        c.find('failures', 1)
        return true
      case 's':
        c.find('skills', 1)
        return true
      case 'S':
        c.find('skills', -1)
        return true
      case 'a':
        c.find('agents', 1)
        return true
      default:
        return false
    }
  })
  useEscape('detail', () => false)

  const ctx: DetailCtx = useMemo(
    () => ({
      session: c.session,
      tz: c.tz,
      goTo: (id) => c.goTo(id, c.cursorNode?.row.type === 'agent' ? c.cursorNode.id : undefined),
      lookup: (id) => c.nodes.find((n) => n.id === id)?.row,
      toolNames: c.toolNames,
      firstTurnIsHuman: c.turnRow?.kind === 'human' || c.turnRow === undefined, // FALLBACK-OK: TRN-34 (before the list arrives the reason wording is the "your" form)
    }),
    [c],
  )

  const common = {
    kind,
    turnIx: c.ix,
    onTurn: () => nav.closeTo('turn'),
    onPrev: () => c.move(-1),
    onNext: () => c.move(1),
    onClose: () => nav.closeTo('turn'),
  }

  let body: React.ReactNode
  if (c.rowsError && what !== 'message') {
    body = (
      <>
        <DetailHeader {...common} inside={null} clock={null} stepper={null} title={null} />
        <ErrorCard noun="the turn's rows" message={c.rowsError.message} onRetry={c.rowsError.retry} />
      </>
    )
  } else if (what === null || (c.rows === undefined && what !== 'message')) {
    body = <Frame header={<DetailHeader {...common} inside={null} clock={null} stepper={null} title={null} />}><SkeletonHero /></Frame>
  } else if (what === 'message') {
    body = <MessageBody key={step} c={c} ctx={ctx} common={common} kind={kind} />
  } else if (what === 'gap') {
    body = <GapBody key={step} c={c} ctx={ctx} common={common} step={step!} />
  } else {
    body = <OpBody key={step} c={c} ctx={ctx} common={common} step={step!} url={url.zoom} />
  }
  return (
    <div className="flex h-full flex-col" data-detail>
      {body}
    </div>
  )
}

type Common = {
  kind: KindInfo | null
  turnIx: number
  onTurn: () => void
  onPrev: () => void
  onNext: () => void
  onClose: () => void
}

function Frame({ header, children }: { header: React.ReactNode; children: React.ReactNode }) {
  return (
    <>
      {header}
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">{children}</div>
    </>
  )
}

function SkeletonHero() {
  return (
    <Hero>
      <Skeleton width={140} height={36} />
      <Skeleton width={180} height={16} />
    </Hero>
  )
}

/** The row as the turn blade holds it, or (inside a band that is not loaded) the op detail's copy of its fields. */
function asRow(d: Detail | undefined): AnyRow | undefined {
  if (d === undefined || d.kind === 'message') return undefined
  return { ...d, type: d.kind } as unknown as AnyRow
}

function OpBody({ c, ctx, common, step, url: zoom }: { c: TurnCursor; ctx: DetailCtx; common: Common; step: string; url: { start_ms: number; end_ms: number } | null }) {
  const q = useApi<Detail>(urls.opDetail(step), (signal) => client.opDetail(step, signal))
  const listed = c.nodes.find((n) => n.id === step)
  const detail = q.data !== undefined && !q.stale && q.data.position.row.id === step ? q.data : undefined
  const row = listed?.row ?? asRow(detail)
  const err: LoadError | null = q.error ? { message: q.error.message, retry: q.retry } : null
  const turnCost = c.rows?.turn.cost.cost_usd ?? null // FALLBACK-OK: DET-10 (no turn rows yet: the op detail's turn cost stands in)
  useCrumb('detail', row ? clip(rowTitle(row, ctx).text, 40) : null)

  const title = row ? rowTitle(row, ctx) : null
  const start = row ? rowStart(row) : null
  const header = (
    <DetailHeader
      {...common}
      inside={c.inside}
      clock={start === null ? null : clockSec(start, c.tz)}
      stepper={{ r: listed ? c.pos.r : 0, m: c.pos.m }}
      title={title}
    />
  )
  if (row === undefined && err) {
    return <Frame header={header}><ErrorCard noun="details" message={err.message} onRetry={err.retry} /></Frame>
  }
  const wire = (kind: Detail['kind']) => (detail?.kind === kind ? detail : undefined)
  const type = row?.type ?? detail?.kind
  return (
    <Frame header={header}>
      {type === 'call' ? (
        <CallDetailView ctx={ctx} row={row as CallRow | undefined} detail={wire('call') as Extract<Detail, { kind: 'call' }> | undefined} error={err} turnCost={turnCost} />
      ) : type === 'tool' ? (
        <ToolDetailView session={c.session} row={row as ToolRow | undefined} detail={wire('tool') as Extract<Detail, { kind: 'tool' }> | undefined} error={err} />
      ) : type === 'agent' ? (
        <AgentDetailView
          ctx={ctx}
          row={row as AgentRow | undefined}
          detail={wire('agent') as Extract<Detail, { kind: 'agent' }> | undefined}
          error={err}
          turnCost={turnCost}
          zoom={zoom ? { agentCost: c.rangeAgents?.get(step)?.cost_usd ?? null, rangeCost: c.range?.cost.cost_usd ?? null } : null /* FALLBACK-OK: DET-30 (no figures for the range yet: the caption is left out) */}
          rowsShown={c.bands.open.has(step)}
          onToggleRows={() => c.bands.setOpen(step, !c.bands.open.has(step))}
        />
      ) : (
        <SkeletonHero />
      )}
    </Frame>
  )
}

function GapBody({ c, ctx, common, step }: { c: TurnCursor; ctx: DetailCtx; common: Common; step: string }) {
  const listed = c.nodes.find((n) => n.id === step)
  const gap = listed?.row as GapRow | undefined
  // The rows next to the gap, in As-it-ran order, among the rows of the same list (the turn's own, or one agent's band).
  const siblings = useMemo(() => (listed ? c.nodes.filter((n) => n.ancestors.join('/') === listed.ancestors.join('/')) : []), [c.nodes, listed])
  const at = listed ? siblings.findIndex((n) => n.id === step) : -1
  const before = at > 0 ? siblings[at - 1].row : null
  const sameTurnAfter = at >= 0 && at < siblings.length - 1 ? siblings[at + 1].row : null
  // A gap that ends the turn is followed by the next turn's first row.
  const next: NextTurn | null = gap?.next_turn ?? null
  const needNext = gap !== undefined && sameTurnAfter === null && next !== null && listed!.ancestors.length === 0
  const nextQ = useApi<TurnRows>(needNext ? urls.turnRows(next!.turn) : null, (signal) => client.turnRows(next!.turn, undefined, signal))
  const nextFirst = useMemo(() => (nextQ.data ? (buildNodes(nextQ.data.rows)[0]?.row ?? null) : null), [nextQ.data]) // FALLBACK-OK: DET-42 (a next turn with no rows has no first row to link)
  useCrumb('detail', gap ? clip(rowTitle(gap, ctx).text, 40) : null)
  const header = (
    <DetailHeader
      {...common}
      inside={c.inside}
      clock={gap ? clockRange(gap.start_ms, gap.end_ms, c.tz) : null}
      stepper={{ r: listed ? c.pos.r : 0, m: c.pos.m }}
      title={gap ? rowTitle(gap, ctx) : null}
    />
  )
  if (gap === undefined) {
    return <Frame header={header}><SkeletonHero /></Frame>
  }
  const turnWall = c.rows?.turn.wall_ms ?? c.turnRow?.wall_ms ?? null // FALLBACK-OK: DET-40 (no turn wall known: no caption)
  return (
    <Frame header={header}>
      <GapDetailView
        ctx={ctx}
        gap={gap}
        turnWallMs={turnWall}
        sessionWallMs={c.sessionWallMs}
        before={before}
        after={sameTurnAfter ?? nextFirst}
        startsTurn={sameTurnAfter === null && next !== null && listed!.ancestors.length === 0 ? next : null}
        sessionEnd={sameTurnAfter === null && next === null && listed!.ancestors.length === 0}
      />
    </Frame>
  )
}

function MessageBody({ c, ctx, common, kind }: { c: TurnCursor; ctx: DetailCtx; common: Common; kind: KindInfo | null }) {
  const turnOp = c.turnOp!
  const viaDetail = useApi<Detail>(c.rows ? null : urls.opDetail(turnOp), (signal) => client.opDetail(turnOp, signal))
  const wire = viaDetail.data?.kind === 'message' ? viaDetail.data : undefined
  const textSrc = c.rows?.turn.text_src ?? wire?.text_src ?? null // FALLBACK-OK: DET-50 (the turn's rows carry the pointer; else the op detail's message does)
  const label = c.turnRow?.label ?? wire?.label ?? ''
  const origin = c.turnRow?.origin ?? wire?.turn_origin ?? ''
  const info = kind ?? (wire ? kindOf(wire.turn_kind, wire.turn_origin, wire.label) : null)
  const text = useSourceText(c.session, textSrc, 'text')
  const sender = senderOf(origin, text.status === 'ready' ? text.data.text : null)
  useCrumb('detail', info ? info.label : null)
  const start = c.turnRow?.start_ms ?? null
  const header = (
    <DetailHeader
      {...common}
      kind={info}
      inside={null}
      clock={start === null ? null : clockSec(start, c.tz)}
      stepper={null}
      title={info ? { glyph: info.icon, text: info.label } : null}
      sub={sender ? `from ${sender}` : null}
    />
  )
  void ctx
  if (textSrc === null) {
    return (
      <Frame header={header}>
        {viaDetail.error ? <ErrorCard noun="the message" message={viaDetail.error.message} onRetry={viaDetail.retry} /> : <SkeletonHero />}
      </Frame>
    )
  }
  return (
    <Frame header={header}>
      <MessageDetailView session={c.session} textSrc={textSrc} slash={info?.key === 'slash' ? label : null} />
    </Frame>
  )
}
