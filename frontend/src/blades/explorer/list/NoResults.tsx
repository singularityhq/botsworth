import { client, urls } from '../../../api/client'
import type { Provider, Scope } from '../../../api/types'
import { useApi } from '../../../api/useApi'
import { fmt } from '../../../lib/format'
import { modelShort } from '../../../lib/model'
import { useNav } from '../../../state/nav'
import { useUrlState } from '../../../state/url'
import { EmptyState } from '../../../ui/EmptyState'
import { Icon } from '../../../ui/Icon'
import { ProjectDot } from '../../../ui/ProjectDot'
import { PROVIDER_NAME } from '../../../ui/provider'
import { ProviderMark } from '../../../ui/ProviderMark'
import { allTimeMatchText, noResultKind } from './list.logic'

const monthDay = (day: string) => fmt.date(day).slice(4)

function Chip({ label, onRemove, children }: { label: string; onRemove: () => void; children: React.ReactNode }) {
  return (
    <span className="inline-flex h-8 items-center gap-1.5 rounded-chip bg-l3 pr-1 pl-2 text-body text-ink1">
      {children}
      <span className="max-w-[240px] truncate">{label}</span>
      <button type="button" aria-label={`Remove ${label} filter`} onClick={onRemove} className="group/x inline-flex size-5 items-center justify-center rounded-chip">
        <Icon name="x" tone="muted" className="group-hover/x:text-ink1" />
      </button>
    </span>
  )
}

/** The project filter holds the raw cwd (D1); the chip shows its last path segment. */
const projectLabel = (key: string) => key.split('/').filter(Boolean).pop() ?? key // FALLBACK-OK: explorer.md EXP-25 (a key with no segments shows as itself)

/**
 * EXP-79, EXP-80: the list region when no session is in scope. With search text or a filter active it says so, repeats the tokens,
 * offers Clear filters and the all-time match count. With none active it is an empty period and offers the nearest days that have sessions.
 */
export function NoResults({ scope }: { scope: Scope }) {
  const url = useUrlState()
  const nav = useNav()
  const summary = useApi(urls.explorerSummary(scope), (signal) => client.explorerSummary(scope, signal))
  const empty = summary.data?.empty ?? null
  const day = (d: string) => nav.replace({ period: { kind: 'custom', from: d, to: d } })

  if (noResultKind(scope) === 'empty-period') {
    const { from_day: from, to_day: to } = scope
    const title = from === null || to === null ? 'No sessions yet' : from === to ? `No sessions on ${fmt.date(from)}` : `No sessions from ${monthDay(from)} to ${monthDay(to)}`
    const near = [empty?.prev_day, empty?.next_day].filter((d): d is NonNullable<typeof d> => !!d)
    return (
      <div className="flex flex-col items-center gap-3 p-6">
        <EmptyState icon="calendar-x">{title}</EmptyState>
        {near.length > 0 ? (
          <div className="flex gap-3">
            {near.map((d) => (
              <button key={d.day} type="button" onClick={() => day(d.day)} className="h-8 rounded-control border border-struct-line px-3 text-body text-struct hover:bg-struct-soft">
                {fmt.date(d.day)} · {d.n_sessions} {d.n_sessions === 1 ? 'session' : 'sessions'}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    )
  }

  const clear = () => nav.replace({ q: '', providers: [], models: [], branches: [], projects: [], auto: false })
  const matches = empty?.all_time_matches ?? null // FALLBACK-OK: interfaces.md QRY-24 (null when the search text is empty, or while the count loads)
  return (
    <div className="flex flex-col items-center gap-3 p-8 text-center">
      <Icon name="search-x" size={24} tone="muted" />
      <p className="text-lead font-semibold text-ink1">No sessions match</p>
      <div className="flex flex-wrap justify-center gap-2">
        {url.q !== '' ? (
          <Chip label={`“${url.q}”`} onRemove={() => nav.replace({ q: '' })}>
            <Icon name="search" tone="muted" />
          </Chip>
        ) : null}
        {url.providers.map((p: Provider) => (
          <Chip key={p} label={PROVIDER_NAME[p]} onRemove={() => nav.replace({ providers: url.providers.filter((x) => x !== p) })}>
            <ProviderMark provider={p} label={false} />
          </Chip>
        ))}
        {url.models.map((m) => (
          <Chip key={m} label={modelShort(m)} onRemove={() => nav.replace({ models: url.models.filter((x) => x !== m) })}>
            <Icon name="cpu" tone="muted" />
          </Chip>
        ))}
        {url.branches.map((b) => (
          <Chip key={b} label={b} onRemove={() => nav.replace({ branches: url.branches.filter((x) => x !== b) })}>
            <Icon name="git-branch" tone="muted" />
          </Chip>
        ))}
        {url.projects.map((p) => (
          <Chip key={p} label={projectLabel(p)} onRemove={() => nav.replace({ projects: url.projects.filter((x) => x !== p) })}>
            <ProjectDot slot={null} />
          </Chip>
        ))}
      </div>
      <button type="button" onClick={clear} className="h-8 rounded-control border border-struct-line px-3 text-body text-struct hover:bg-struct-soft">
        Clear filters
      </button>
      {url.q !== '' && matches !== null ? (
        <p className="text-body text-ink2">
          {allTimeMatchText(url.q, matches)}
          {matches > 0 ? (
            <>
              {' · '}
              <button type="button" onClick={() => nav.replace({ period: { kind: 'preset', preset: 'all' } })} className="text-struct hover:underline">
                Show
              </button>
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  )
}
