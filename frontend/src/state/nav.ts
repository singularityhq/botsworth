import { useMemo } from 'react'
import { browserUrl, DEFAULT_URL_STATE, LEVEL_PARAMS, normalizeUrlState, openBlades, parseUrl, serializeUrl } from './url'
import type { BladeLevel, UrlEnv, UrlState } from './url'

/**
 * URL writes. Opening a blade adds a history entry; every other change
 * rewrites the current one (EXP-76, SES-10), so Back closes the rightmost
 * blade. Closing a blade undoes the push that opened it (history.back())
 * when this tab made that push, and rewrites the entry otherwise (deep link,
 * reload), so one visible change is one history step.
 */

/** Identifies this page load; entries pushed by an earlier load or tab do not carry it. */
const TAB = Math.random().toString(36).slice(2)
interface OpenedTag {
  opened: BladeLevel
  tab: string
}
function isOpenedTag(v: unknown): v is OpenedTag {
  return typeof v === 'object' && v !== null && (v as OpenedTag).tab === TAB && typeof (v as OpenedTag).opened === 'string'
}

export type Patch = Partial<UrlState>
type BladeParam = Exclude<BladeLevel, 'explorer'>

export interface Nav {
  /** Change state in place: scope, cursor, measure, view, search, range, zoom. */
  replace(patch: Patch): void
  /** Add a history entry with the patch applied. */
  push(patch: Patch): void
  /**
   * Open the blade at `level` with `patch`. The blade's own parameters and
   * those of every deeper blade return to their defaults first.
   */
  openBlade(level: BladeParam, patch: Patch): void
  /**
   * Close every blade deeper than `level`. Goes back one history step when
   * exactly one blade closes and the current entry was pushed by opening it.
   */
  closeTo(level: BladeLevel): void
}

const ORDER: readonly BladeParam[] = ['session', 'turn', 'detail']

function reset(state: UrlState, levels: readonly BladeParam[]): UrlState {
  const next: Record<string, unknown> = { ...state }
  for (const level of levels) for (const key of LEVEL_PARAMS[level]) next[key] = DEFAULT_URL_STATE[key]
  return next as unknown as UrlState
}

export function createNav(env: UrlEnv): Nav {
  const current = () => parseUrl(env.getSearch())
  const write = (state: UrlState, how: 'push' | 'replace', tag?: OpenedTag) => {
    const query = serializeUrl(normalizeUrlState(state))
    if (query === env.getSearch().replace(/^\?/, '')) return // same URL: no new entry
    if (how === 'push') env.push(query, tag)
    else env.replace(query)
  }
  return {
    replace: (patch) => write({ ...current(), ...patch }, 'replace'),
    push: (patch) => write({ ...current(), ...patch }, 'push'),
    openBlade: (level, patch) =>
      write({ ...reset(current(), ORDER.slice(ORDER.indexOf(level))), ...patch }, 'push', { opened: level, tab: TAB }),
    closeTo: (level) => {
      const cur = current()
      const open = openBlades(cur)
      const top = open[open.length - 1]
      const tag = env.getState()
      if (top !== 'explorer' && open.indexOf(top) - open.indexOf(level) === 1 && isOpenedTag(tag) && tag.opened === top) {
        env.back()
        return
      }
      write(reset(cur, level === 'explorer' ? ORDER : ORDER.slice(ORDER.indexOf(level) + 1)), 'replace')
    },
  }
}

export function useNav(): Nav {
  return useMemo(() => createNav(browserUrl), [])
}
