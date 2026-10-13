import { useEffect, useMemo, useRef, useState } from 'react'
import { client, urls } from '../../../api/client'
import type { Day, Provider, Scope } from '../../../api/types'
import { useApi, useDebounced } from '../../../api/useApi'
import { fmt } from '../../../lib/format'
import { useNav } from '../../../state/nav'
import { todayIn } from '../../../state/period'
import { useBrowserZone } from '../../../state/zone'
import { toScope, useUrlState } from '../../../state/url'
import { Icon } from '../../../ui/Icon'
import type { IconName } from '../../../ui/glyphs'
import { KeyCap } from '../../../ui/KeyCap'
import { useOverlay } from '../../../ui/overlay'
import { ProjectDot } from '../../../ui/ProjectDot'
import { ProviderMark } from '../../../ui/ProviderMark'
import { seriesVar } from '../../../ui/series'
import {
  emptySections, factsText, flatItems, plural, moveSelection, planFor, typedSections,
  type DateItem, type FilterKind, type PaletteItem, type Section, type Undo,
} from './palette.logic'

/**
 * The sub-line of a date row. One day: that day's facts from the day series, under the current filters (FE-10, QRY-10). A range: the
 * period summary of that range, because a session that spans days would be counted once per day if the day rows were added up.
 */
function DateFacts({ item, scope }: { item: DateItem; scope: Scope }) {
  const single = item.from !== null && item.from === item.to
  const daysKey = single ? urls.explorerDays(scope, { from: item.from!, to: item.to! }) : null
  const rangeScope: Scope = { ...scope, from_day: item.from, to_day: item.to }
  const summaryKey = single ? null : urls.explorerSummary(rangeScope)
  const days = useApi(daysKey, (signal) => client.explorerDays(scope, { from: item.from!, to: item.to! }, signal))
  const summary = useApi(summaryKey, (signal) => client.explorerSummary(rangeScope, signal))
  const d = days.data?.days[0]
  if (d) return <>{d.n_sessions === 0 ? 'No sessions' : factsText(d.n_sessions, d.cost_usd, d.cost_lower_bound, d.tok_total)}</>
  const t = summary.data?.totals
  if (t) return <>{t.n_sessions === 0 ? 'No sessions' : factsText(t.n_sessions, t.cost_usd, t.cost_lower_bound, t.tok_total)}</>
  return <>{item.from === null ? 'Every session' : ' '}</>
}

const FILTER_ICON: Record<Exclude<FilterKind, 'provider' | 'project'>, IconName> = { model: 'cpu', branch: 'git-branch' }

function Glyph({ item }: { item: PaletteItem }) {
  switch (item.kind) {
    case 'date':
      return <Icon name="calendar" tone="muted" />
    case 'prefix':
    case 'filter':
      if (item.filter === 'provider') {
        const p: Provider = item.kind === 'filter' ? (item.value as Provider) : 'claude_code'
        return <ProviderMark provider={p} label={false} />
      }
      if (item.filter === 'project') return <ProjectDot slot={null} />
      return <Icon name={FILTER_ICON[item.filter]} tone="muted" />
    case 'project':
      return <ProjectDot slot={item.row.color_slot} />
    case 'session':
      return <ProviderMark provider={item.row.provider} label={false} />
  }
}

function RowBody({ item, scope, tz }: { item: PaletteItem; scope: Scope; tz: string }) {
  switch (item.kind) {
    case 'date':
      return (
        <>
          <span className="min-w-0">
            <span className="block truncate text-ink1">{item.label}</span>
            <span className="block truncate text-ink3">
              <DateFacts item={item} scope={scope} />
            </span>
          </span>
          <span className="fig text-ink3">{item.from !== null && item.to !== null && item.from !== item.to ? fmt.dateRange(item.from, item.to) : ''}</span>
        </>
      )
    case 'prefix':
      return (
        <>
          <span className="truncate text-ink1">{item.filter}:</span>
          <span className="fig text-ink3">{item.example}</span>
        </>
      )
    case 'filter':
      return (
        <>
          <span className="truncate text-ink1">{item.label}</span>
          <span className="text-ink3">{plural(item.n, 'session')}</span>
        </>
      )
    case 'project':
      return (
        <>
          <span className="min-w-0">
            <span className="block truncate text-ink1">{item.row.project_name}</span>
            <span className="block truncate text-ink3">{plural(item.row.n_sessions, 'session')}</span>
          </span>
          <span className="fig text-money">{fmt.money(item.row.cost_usd, { lowerBound: item.row.cost_lower_bound })}</span>
        </>
      )
    case 'session': {
      const r = item.row
      return (
        <>
          <span className="min-w-0">
            <span className="block truncate text-ink1">{r.name}</span>
            <span className="flex items-center gap-1.5 truncate text-ink3">
              <ProjectDot slot={r.color_slot} />
              <span className="truncate" style={{ color: seriesVar(r.color_slot) }}>
                {r.project_name}
              </span>
              <span className="whitespace-nowrap">
                {fmt.date(r.start_ms, tz)} {fmt.clock(r.start_ms, tz)}
              </span>
            </span>
          </span>
          <span className="fig text-money">{fmt.money(r.cost_usd, { lowerBound: r.cost_lower_bound })}</span>
        </>
      )
    }
  }
}

/** EXP-67 to EXP-72: the ⌘K palette. */
export function Palette({ onClose, onToast }: { onClose: () => void; onToast: (u: Undo) => void }) {
  const nav = useNav()
  const url = useUrlState()
  const tz = useBrowserZone()
  const today: Day = todayIn(tz, Date.now())
  const scope: Scope = toScope(url, today)

  const [text, setText] = useState('')
  const [sel, setSel] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  useOverlay(true, onClose)
  useEffect(() => input.current?.focus(), [])

  // FE-10: palette requests wait 100 ms after the last keystroke.
  const typed = useDebounced(text.trim(), 100)
  const pal = useApi(typed !== '' ? urls.palette(typed, scope) : null, (signal) => client.palette(typed, scope, signal))
  const largestKey = typed === '' ? urls.explorerSessions(scope, { sort: 'cost', limit: 6 }) : null
  const largest = useApi(largestKey, (signal) => client.explorerSessions(scope, { sort: 'cost', limit: 6 }, signal))

  const sections: Section[] = useMemo(() => {
    if (!today) return []
    return typed === '' ? emptySections(today, largest.data?.rows ?? []) : typedSections(typed, today, pal.data) // FALLBACK-OK: explorer.md EXP-68 (the Largest section appears once its rows arrive)
  }, [today, typed, largest.data, pal.data])
  const items = useMemo(() => flatItems(sections), [sections])
  const at = items.length === 0 ? 0 : Math.min(sel, items.length - 1)
  useEffect(() => setSel(0), [typed])
  useEffect(() => {
    list.current?.querySelector('[data-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [at, items])

  const run = (item: PaletteItem) => {
    if (!today) return
    const plan = planFor(item, { url, today })
    if (plan.type === 'prefix') {
      setText(plan.text)
      input.current?.focus()
      return
    }
    if (plan.type === 'scope') nav.replace(plan.patch)
    else {
      nav.openBlade('session', plan.patch)
      if (plan.toast) onToast(plan.toast)
    }
    onClose()
  }

  const settled = typed === '' ? true : pal.data !== undefined && !pal.stale
  let index = 0
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-ground/70 pt-[14vh]" onPointerDown={onClose}>
      <div
        role="dialog"
        aria-label="Command palette"
        className="pop-in flex max-h-[640px] w-[660px] flex-col overflow-hidden rounded-card bg-l3 shadow-pop"
        onPointerDown={(e) => e.stopPropagation()}
      >
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-rule px-4">
          <Icon name="search" size={20} tone="muted" />
          <input
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault()
                setSel(moveSelection(at, e.key === 'ArrowDown' ? 1 : -1, items.length))
              } else if (e.key === 'Enter') {
                e.preventDefault()
                if (items[at]) run(items[at])
              }
            }}
            placeholder="Jump to a session, project or date"
            aria-label="Jump to a session, project or date"
            autoComplete="off"
            spellCheck={false}
            data-focus-quiet
            className="h-full min-w-0 flex-1 bg-transparent text-lead text-ink1 placeholder:text-ink3"
          />
        </div>
        <div ref={list} className="min-h-0 flex-1 overflow-y-auto pb-2">
          {sections.map((s) => (
            <section key={s.id} aria-label={s.title}>
              <h3 className="sticky top-0 z-10 bg-l3 px-4 pt-3 pb-1 text-body font-semibold text-ink1">{s.title}</h3>
              <div className="px-2">
                {s.items.map((item) => {
                  const i = index++
                  const on = i === at
                  return (
                    <div
                      key={item.key}
                      role="button"
                      tabIndex={-1}
                      data-selected={on}
                      onPointerMove={() => i !== at && setSel(i)}
                      onClick={() => run(item)}
                      className={`relative grid ${item.kind === 'prefix' || item.kind === 'filter' ? 'min-h-10' : 'min-h-12'} cursor-pointer grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-3 rounded-control px-2.5 py-1.5 text-body ${on ? 'bg-l4' : ''}`}
                    >
                      {on ? <span aria-hidden="true" className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-struct" /> : null}
                      <Glyph item={item} />
                      <RowBody item={item} scope={scope} tz={tz} />
                    </div>
                  )
                })}
              </div>
            </section>
          ))}
          {typed !== '' && settled && items.length === 0 ? <p className="px-4 py-6 text-body text-ink2">Nothing matches “{typed}”.</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-4 border-t border-rule px-4 py-2.5 text-body text-ink3">
          <span className="flex items-center gap-1.5">
            <KeyCap>↑</KeyCap>
            <KeyCap>↓</KeyCap>
            move
          </span>
          <span className="flex items-center gap-1.5">
            <KeyCap>↵</KeyCap>
            open
          </span>
          <span className="flex items-center gap-1.5">
            <KeyCap>esc</KeyCap>
            close
          </span>
        </div>
      </div>
    </div>
  )
}
