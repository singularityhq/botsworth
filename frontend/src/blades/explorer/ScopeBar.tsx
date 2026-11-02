import type { RefObject } from 'react'
import type { Provider } from '../../api/types'
import { Icon } from '../../ui/Icon'
import { KeyCap } from '../../ui/KeyCap'
import { ProjectDot } from '../../ui/ProjectDot'
import { ProviderMark } from '../../ui/ProviderMark'
import type { Slot } from '../../ui/series'
import { middleTruncate, type FilterToken } from './explorer.logic'

/** EXP-17 to EXP-26: the search field, the filter tokens, the Filter button and Clear all. */

/** EXP-17: 280 px wide, 360 px while focused (260 ms, none under reduced motion), 32 px tall; `/` key cap at the end, an `x` when it holds text. */
export function SearchField({ value, onChange, inputRef }: { value: string; onChange: (v: string) => void; inputRef: RefObject<HTMLInputElement | null> }) {
  return (
    <label className="relative flex h-8 w-[280px] shrink-0 items-center gap-2 rounded-control border border-edge bg-l1 pr-2 pl-2.5 text-body transition-[width,border-color] duration-[260ms] ease-[var(--ease-standard)] focus-within:w-[360px] focus-within:border-struct-line">
      <Icon name="search" tone="muted" />
      <input
        ref={inputRef}
        data-focus-quiet
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search sessions"
        aria-label="Search sessions"
        spellCheck={false}
        autoComplete="off"
        className="h-6 min-w-0 flex-1 bg-transparent text-body text-ink1 placeholder:text-ink3 focus:outline-none"
      />
      {value !== '' ? (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            onChange('')
            inputRef.current?.focus()
          }}
          className="inline-flex size-6 items-center justify-center rounded-chip text-ink3 hover:text-ink1"
        >
          <Icon name="x" tone="inherit" />
        </button>
      ) : (
        <KeyCap>/</KeyCap>
      )}
    </label>
  )
}

function Leading({ token, slot }: { token: FilterToken; slot: Slot }) {
  switch (token.kind) {
    case 'provider':
      return <ProviderMark provider={token.value as Provider} label={false} />
    case 'model':
      return <Icon name="cpu" tone="muted" />
    case 'branch':
      return <Icon name="git-branch" tone="muted" />
    case 'project':
      return <ProjectDot slot={slot} />
    case 'automated':
      return <Icon name="repeat" tone="muted" />
  }
}

/** EXP-25: one 32 px token per selected value, l3 fill, leading identifier, trailing `x`. */
export function FilterTokens({ tokens, onRemove, projectSlot }: { tokens: readonly FilterToken[]; onRemove: (t: FilterToken) => void; projectSlot: (key: string) => Slot }) {
  return (
    <>
      {tokens.map((t) => (
        <span key={`${t.kind}:${t.value}`} className="inline-flex h-8 max-w-[260px] items-center gap-2 rounded-chip bg-l3 pr-1 pl-2.5 text-body text-ink1">
          <Leading token={t} slot={t.kind === 'project' ? projectSlot(t.value) : null} />
          <span className="truncate" title={t.kind === 'branch' ? t.label : undefined}>
            {t.kind === 'branch' ? middleTruncate(t.label, 28) : t.label}
          </span>
          <button
            type="button"
            aria-label={`Remove ${t.label} filter`}
            onClick={() => onRemove(t)}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-chip text-ink3 hover:text-ink1"
          >
            <Icon name="x" tone="inherit" />
          </button>
        </span>
      ))}
    </>
  )
}

export interface ScopeBarProps {
  q: string
  onQuery: (q: string) => void
  searchRef: RefObject<HTMLInputElement | null>
  tokens: readonly FilterToken[]
  projectSlot: (key: string) => Slot
  onRemove: (t: FilterToken) => void
  filterRef: RefObject<HTMLButtonElement | null>
  filterOpen: boolean
  onFilter: () => void
  showClear: boolean
  onClear: () => void
}

export function ScopeBar(p: ScopeBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SearchField inputRef={p.searchRef} value={p.q} onChange={p.onQuery} />
      <FilterTokens tokens={p.tokens} onRemove={p.onRemove} projectSlot={p.projectSlot} />
      <button
        ref={p.filterRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={p.filterOpen}
        onClick={p.onFilter}
        className="inline-flex h-8 items-center gap-2 rounded-control border border-edge bg-l1 px-3 text-body text-ink1 transition-colors duration-150 hover:border-struct-line hover:bg-l2"
      >
        <Icon name="sliders-horizontal" tone="quiet" />
        Filter
      </button>
      {p.showClear ? (
        <button type="button" onClick={p.onClear} className="h-8 px-2 text-body text-ink3 transition-colors duration-150 hover:text-struct">
          Clear all
        </button>
      ) : null}
    </div>
  )
}
