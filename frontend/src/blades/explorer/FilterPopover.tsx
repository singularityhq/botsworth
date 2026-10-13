import { useState, type RefObject } from 'react'
import { client, urls } from '../../api/client'
import type { Facets, Provider, Scope } from '../../api/types'
import { useApi } from '../../api/useApi'
import { modelShort } from '../../lib/format'
import type { Patch } from '../../state/nav'
import type { UrlState } from '../../state/url'
import { Icon } from '../../ui/Icon'
import type { IconName } from '../../ui/glyphs'
import { Popover } from '../../ui/Popover'
import { PROVIDER_NAME } from '../../ui/provider'
import { ProviderMark } from '../../ui/ProviderMark'
import { SkeletonRows } from '../../ui/Skeleton'
import { middleTruncate, toggleValue } from './explorer.logic'
import { presentProviders, visibleFacets, type FacetRow } from './filterPopover.logic'
import { RegionError } from './explorerStates'

/** EXP-20 to EXP-24: one column with Provider, Model, Branch and Automated. Facet counts are "except own" (QRY-8): one request. */

function Head({ icon, children }: { icon: IconName; children: React.ReactNode }) {
  return (
    <h3 className="m-0 mb-2 flex items-center gap-2 text-body font-semibold text-ink1">
      <Icon name={icon} tone="quiet" />
      {children}
    </h3>
  )
}

function ValueRow({ label, title, n, on, onClick }: { label: string; title?: string; n: number; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      title={title}
      onClick={onClick}
      className={`flex h-8 w-full items-center gap-2 rounded-control px-2 text-body transition-colors duration-150 ${on ? 'bg-struct-soft text-struct' : 'text-ink1 hover:bg-l4'}`}
    >
      <span className="inline-flex w-4 shrink-0">{on ? <Icon name="check" tone="struct" /> : null}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className={`fig ${on ? 'text-struct' : 'text-ink3'}`}>{n}</span>
    </button>
  )
}

function ShowAll({ n, open, onToggle }: { n: number; open: boolean; onToggle: () => void }) {
  if (n === 0 && !open) return null
  return (
    <button type="button" onClick={onToggle} className="mt-1 h-8 px-2 text-body text-struct hover:underline">
      {open ? 'Show fewer' : `Show all (${n})`}
    </button>
  )
}

function FacetSection({
  icon, title, rows, selected, onToggle, label, search,
}: {
  icon: IconName
  title: string
  rows: readonly FacetRow[]
  selected: readonly string[]
  onToggle: (v: string) => void
  label: (v: string) => { text: string; title?: string }
  search?: boolean
}) {
  const [showAll, setShowAll] = useState(false)
  const [text, setText] = useState('')
  const list = visibleFacets(rows, selected, showAll, text)
  return (
    <section>
      <Head icon={icon}>{title}</Head>
      {search ? (
        <input
          data-focus-quiet
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type to narrow"
          aria-label="Narrow branches"
          spellCheck={false}
          autoComplete="off"
          className="mb-2 h-8 w-full rounded-control border border-edge bg-l2 px-2.5 text-body text-ink1 placeholder:text-ink3 focus:border-struct-line focus:outline-none"
        />
      ) : null}
      <div className="max-h-44 overflow-y-auto">
        {list.rows.map((r) => {
          const l = label(r.value)
          return <ValueRow key={r.value} label={l.text} title={l.title} n={r.n} on={selected.includes(r.value)} onClick={() => onToggle(r.value)} />
        })}
        {list.rows.length === 0 ? <p className="m-0 px-2 py-1 text-body text-ink3">None in this period.</p> : null}
      </div>
      <ShowAll n={list.hiddenCount} open={showAll} onToggle={() => setShowAll((v) => !v)} />
    </section>
  )
}

function Body({ url, facets, onPatch }: { url: UrlState; facets: Facets; onPatch: (p: Patch) => void }) {
  return (
    <div className="flex flex-col gap-4 p-2">
      <section>
        <Head icon="layers">Provider</Head>
        <div className="flex flex-wrap gap-2">
          {presentProviders(facets, url.providers).map((p) => {
            const on = url.providers.includes(p.value)
            return (
              <button
                key={p.value}
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => onPatch({ providers: toggleValue(url.providers, p.value) as Provider[] })}
                className={`inline-flex h-8 items-center gap-2 rounded-control border px-2.5 text-body transition-colors duration-150 ${
                  on ? 'border-struct-line bg-struct-soft text-struct' : 'border-edge text-ink1 hover:bg-l4'
                }`}
              >
                <ProviderMark provider={p.value} label={false} />
                {PROVIDER_NAME[p.value]}
                <span className={`fig ${on ? 'text-struct' : 'text-ink3'}`}>{p.n}</span>
              </button>
            )
          })}
        </div>
      </section>
      <FacetSection
        icon="cpu"
        title="Model · sessions using"
        rows={facets.models}
        selected={url.models}
        onToggle={(v) => onPatch({ models: toggleValue(url.models, v) })}
        label={(v) => ({ text: modelShort(v), title: v })}
      />
      <FacetSection
        icon="git-branch"
        title="Branch"
        rows={facets.branches}
        selected={url.branches}
        onToggle={(v) => onPatch({ branches: toggleValue(url.branches, v) })}
        label={(v) => ({ text: middleTruncate(v, 30), title: v })}
        search
      />
      <section>
        <Head icon="repeat">Automated</Head>
        <button
          type="button"
          role="switch"
          aria-checked={!url.auto}
          onClick={() => onPatch({ auto: !url.auto })}
          className="flex h-8 w-full items-center gap-3 rounded-control px-2 text-body text-ink1 hover:bg-l4"
        >
          <span
            aria-hidden="true"
            className={`relative inline-block h-5 w-9 shrink-0 rounded-full border transition-colors duration-150 ${url.auto ? 'border-edge bg-ground' : 'border-struct-line bg-struct-soft'}`}
          >
            <span className={`absolute top-0.5 left-0.5 size-3.5 rounded-full transition-transform duration-200 ${url.auto ? 'bg-ink2' : 'translate-x-4 bg-struct'}`} />
          </span>
          <span className="flex-1">Hide automated sessions</span>
          <span className="fig text-ink3">{facets.automated.n}</span>
        </button>
      </section>
    </div>
  )
}

function Loader({ scope, url, onPatch }: { scope: Scope; url: UrlState; onPatch: (p: Patch) => void }) {
  const facets = useApi<Facets>(urls.explorerFacets(scope), (signal) => client.explorerFacets(scope, signal))
  if (facets.error) return <RegionError error={facets.error} noun="filters" onRetry={facets.retry} />
  if (!facets.data) return <SkeletonRows count={6} rowHeight={32} />
  return <Body url={url} facets={facets.data} onPatch={onPatch} />
}

export function FilterPopover({
  open, onClose, anchorRef, scope, url, onPatch,
}: { open: boolean; onClose: () => void; anchorRef: RefObject<HTMLElement | null>; scope: Scope | null; url: UrlState; onPatch: (p: Patch) => void }) {
  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={340} align="start" label="Filters">
      {scope ? <Loader scope={scope} url={url} onPatch={onPatch} /> : <SkeletonRows count={6} rowHeight={32} />}
    </Popover>
  )
}
