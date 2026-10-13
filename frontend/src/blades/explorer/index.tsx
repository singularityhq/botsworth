import { useCallback, useMemo, useRef, useState } from 'react'
import type { ApiError, NetError } from '../../api/errors'
import type { Day, SessionRow } from '../../api/types'
import { useNav } from '../../state/nav'
import type { Period } from '../../state/period'
import type { Measure } from '../../state/url'
import { useBrowserZone } from '../../state/zone'
import { SkeletonRows } from '../../ui/Skeleton'
import type { Slot } from '../../ui/series'
import { buildColumns } from './dayChart.logic'
import { DayChart, DayChartSkeleton, MeasureToggle } from './DayChart'
import {
  canClearAll, clearAllPatch, filterTokens, pageState, periodTitle, projectFallbackName, removeTokenPatch, totalsView,
} from './explorer.logic'
import { EmptyPeriod, NoData, NoResults, RegionError } from './explorerStates'
import { FilterPopover } from './FilterPopover'
import { useExplorerRouter } from './keys'
import { SessionList, type SessionListProps as ListProps } from './list/SessionList'
import { PeriodPopover } from './PeriodPopover'
import { PeriodTitle } from './PeriodTitle'
import { ProjectPanel, ProjectPanelSkeleton } from './ProjectPanel'
import { Rail } from './rail/Rail'
import { ScopeBar } from './ScopeBar'
import { TotalsLine, TotalsNoStore, TotalsSkeleton } from './TotalsLine'
import { useExplorerData, useExplorerScope, type ExplorerScope } from './useExplorerData'

type Pop = 'period' | 'filter' | null

/** Blade 0 (EXP-5): at full width the period title, totals, scope bar, day chart, project panel and session list; at 400 px the rail. */
export function ExplorerBlade({ width, compact }: { width: number; compact: boolean }) {
  const s = useExplorerScope()
  const nav = useNav()
  const [pop, setPop] = useState<Pop>(null)
  const [hover, setHover] = useState<SessionRow | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const { url } = s

  useExplorerRouter({
    // EXP-11, EXP-20, EXP-18: p opens the period popover, f the filter popover, / focuses the search field. The page has none of these as a rail.
    key: (e) => {
      if (compact) return false
      if (e.key === 'p') setPop('period')
      else if (e.key === 'f') setPop('filter')
      else if (e.key === '/') searchRef.current?.focus()
      else return false
      return true
    },
    // EXP-46: Esc with no popover, palette, focused field or open blade clears the project filter (the shell calls this only after those).
    escape: () => {
      if (compact || url.projects.length === 0) return false
      nav.replace({ projects: [] })
      return true
    },
  })

  const listProps: ListProps | null = s.ready
    ? { scope: s.scope, sort: url.sort, dir: url.dir, group: url.group, onHoverSession: setHover }
    : null

  if (compact) {
    if (s.status.error) return <RegionError error={s.status.error} noun="sessions" onRetry={s.status.retry} />
    if (!s.status.data) return <SkeletonRows />
    if (!s.ready) return <NoData />
    return listProps ? <Rail {...listProps} /> : <SkeletonRows />
  }
  return <ExplorerPage s={s} width={width} pop={pop} setPop={setPop} searchRef={searchRef} hover={hover} listProps={listProps} />
}

function ExplorerPage({
  s, width, pop, setPop, searchRef, hover, listProps,
}: {
  s: ExplorerScope
  width: number
  pop: Pop
  setPop: (p: Pop) => void
  searchRef: React.RefObject<HTMLInputElement | null>
  hover: SessionRow | null
  listProps: ListProps | null
}) {
  const nav = useNav()
  const { url, status, today, range, scope } = s
  const data = useExplorerData(s)
  const tz = useBrowserZone()
  const [draft, setDraft] = useState<{ from: Day; to: Day } | null>(null)
  const [hoverSlot, setHoverSlot] = useState<number | undefined>(undefined)
  const titleRef = useRef<HTMLButtonElement>(null)
  const filterRef = useRef<HTMLButtonElement>(null)
  const closePop = useCallback(() => setPop(null), [setPop])

  const summary = data.summary.data
  const extent = summary ?? data.panel.data ?? null
  const projectInfo = useMemo(() => new Map((data.panel.data?.projects ?? summary?.projects ?? []).map((p) => [p.project, p])), [data.panel.data, summary])
  const projectName = (key: string) => projectInfo.get(key)?.project_name ?? projectFallbackName(key) // FALLBACK-OK: explorer.md EXP-75 (a project filter that matches nothing keeps its token, named from its path)
  const projectSlot = (key: string): Slot => projectInfo.get(key)?.color_slot ?? null // FALLBACK-OK: explorer.md EXP-75 (same: no slot for an unknown project)

  const noStore = status.data !== undefined && !s.ready
  /** The status failing outranks every region's own state: nothing else can load (EXP-83). */
  const gate = (own: ApiError | NetError | null | undefined): ApiError | NetError | null => status.error ?? own ?? null
  const retryAll = () => {
    status.retry()
    data.summary.retry()
    data.panel.retry()
    data.days.retry()
  }

  const apply = (p: Period) => {
    nav.replace({ period: p })
    closePop()
  }
  const select = useCallback((r: { from: Day; to: Day }) => nav.replace({ period: { kind: 'custom', from: r.from, to: r.to } }), [nav])
  const shownPeriod: Period = draft ? { kind: 'custom', from: draft.from, to: draft.to } : url.period
  const title = periodTitle(shownPeriod, today, extent)

  const columns = useMemo(
    () => (data.days.data && data.window && range ? buildColumns(data.window, data.days.data.days, draft ?? range, url.measure) : null),
    [data.days.data, data.window, range, draft, url.measure],
  )

  const state = pageState(summary, url)
  const tokens = filterTokens(url, summary?.totals.n_automated_hidden ?? 0, projectName) // FALLBACK-OK: explorer.md EXP-27 (the automated token waits for the count)
  const wide = width >= 1000
  const showPanel = state !== 'no-results' && state !== 'empty-period'

  const chartBody = (() => {
    const err = gate(data.days.error)
    if (err) return <RegionError error={err} noun="days" onRetry={retryAll} />
    if (noStore) return <NoData />
    if (!columns) return data.window === null && summary && summary.first_day === null ? <NoData /> : <DayChartSkeleton />
    return (
      <DayChart columns={columns} measure={url.measure} hoverSession={hover} hoverSlot={hoverSlot} draft={draft} onDraft={setDraft} onSelect={select} />
    )
  })()

  const panelBody = (() => {
    const err = gate(data.panel.error)
    if (err) return <RegionError error={err} noun="projects" onRetry={retryAll} />
    if (noStore) return <NoData />
    if (!data.panel.data) return <ProjectPanelSkeleton />
    return (
      <ProjectPanel
        projects={data.panel.data.projects}
        measure={url.measure}
        selected={url.projects}
        onFilter={(projects) => nav.replace({ projects })}
        onHoverSlot={setHoverSlot}
      />
    )
  })()

  const totalsBody = (() => {
    const err = gate(data.summary.error)
    if (err) return <RegionError error={err} noun="totals" onRetry={retryAll} />
    if (noStore) return <TotalsNoStore />
    if (!summary) return <TotalsSkeleton />
    return <TotalsLine view={totalsView(summary, url)} />
  })()

  const listBody = (() => {
    if (status.error) return <RegionError error={status.error} noun="sessions" onRetry={retryAll} />
    if (noStore) return <NoData />
    if (state === 'no-results') {
      return (
        <NoResults
          q={url.q}
          tokens={tokens}
          onRemove={(t) => nav.replace(removeTokenPatch(url, t))}
          onClear={() => nav.replace(clearAllPatch())}
          allTime={summary?.empty?.all_time_matches}
          onShowAllTime={() => nav.replace({ period: { kind: 'preset', preset: 'all' } })}
        />
      )
    }
    if (state === 'empty-period') {
      if (!range || range.from === null || range.to === null) return <NoData />
      return <EmptyPeriod from={range.from} to={range.to} empty={summary?.empty ?? null} onPick={(day) => nav.replace({ period: { kind: 'custom', from: day, to: day } })} />
    }
    return listProps ? <SessionList {...listProps} /> : <SkeletonRows />
  })()

  return (
    // The scroller carries no padding: the list's sticky column band measures from its top edge (SessionList CONTRACT).
    <div data-testid="page" className="h-full overflow-y-auto">
     <div className="p-6">
      <PeriodTitle text={title} buttonRef={titleRef} open={pop === 'period'} onToggle={() => setPop(pop === 'period' ? null : 'period')} />
      <div className="mt-2">{totalsBody}</div>
      <div className="mt-4">
        <ScopeBar
          q={url.q}
          onQuery={(q) => nav.replace({ q })}
          searchRef={searchRef}
          tokens={tokens}
          projectSlot={projectSlot}
          onRemove={(t) => nav.replace(removeTokenPatch(url, t))}
          filterRef={filterRef}
          filterOpen={pop === 'filter'}
          onFilter={() => setPop(pop === 'filter' ? null : 'filter')}
          showClear={canClearAll(url)}
          onClear={() => nav.replace(clearAllPatch())}
        />
      </div>
      <div className={`mt-8 grid gap-8 ${wide && showPanel ? 'grid-cols-[minmax(0,1fr)_460px]' : 'grid-cols-1'}`}>
        <section aria-label="Per day" className="min-w-0">
          <div className="mb-2 flex items-center gap-4">
            <MeasureToggle value={url.measure} onChange={(m: Measure) => nav.replace({ measure: m })} />
            <span className="text-body text-ink3">Click a day · drag across days</span>
          </div>
          {chartBody}
        </section>
        {showPanel ? <div className="min-w-0">{panelBody}</div> : null}
      </div>
      <div className="mt-8">{listBody}</div>
      {status.data ? (
        <PeriodPopover
          open={pop === 'period'}
          onClose={closePop}
          anchorRef={titleRef}
          period={url.period}
          today={today}
          tz={tz}
          extent={extent}
          onApply={apply}
        />
      ) : null}
      <FilterPopover open={pop === 'filter'} onClose={closePop} anchorRef={filterRef} scope={scope} url={url} onPatch={(p) => nav.replace(p)} />
     </div>
    </div>
  )
}

