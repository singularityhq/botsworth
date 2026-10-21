import { client, urls } from '../../api/client'
import type { Evidence, PeriodSummary, ReviewReport } from '../../api/types'
import { useApi } from '../../api/useApi'
import { useBladeKeys } from '../../shell/keys'
import { useNav } from '../../state/nav'
import { useBrowserZone } from '../../state/zone'
import { filterTokens, periodTitle, projectFallbackName } from '../explorer/explorer.logic'
import { useExplorerScope } from '../explorer/useExplorerData'
import { turnNumber } from '../session/review.logic'
import { ReviewView } from '../session/ReviewView'
import { fmt } from '../../lib/format'

/**
 * The scope review blade (REV-20 to REV-24): blade 1 in place of a session. The claim, bars and cards of `scope_review` for the explorer's
 * current scope. It reads the scope from the URL like the explorer, so a scope change refetches (the previous content stays until the new
 * data arrives, EXP-78) and it depends on no session blade.
 */
export function ScopeReviewBlade() {
  const s = useExplorerScope()
  const nav = useNav()
  const tz = useBrowserZone()
  const { scope, url, today } = s
  const review = useApi<ReviewReport>(s.ready ? urls.scopeReview(scope) : null, (signal) => client.scopeReview(scope, signal))
  // The summary supplies the data extent for an "All time" title; the explorer's own request is shared.
  const summary = useApi<PeriodSummary>(s.ready ? urls.explorerSummary(scope) : null, (signal) => client.explorerSummary(scope, signal))
  const r = review.data

  // REV-21: `r` closes the scope review, the same toggle as in the session Review (REV-3).
  useBladeKeys('review', (e) => {
    if (e.key !== 'r') return false
    nav.closeTo('explorer')
    return true
  })

  // REV-22 line 2, from the scope state so it shows in every state: the period, each active filter value (EXP-25 order), the search in quotes, then the count.
  const projectName = (key: string) => summary.data?.projects.find((p) => p.project === key)?.project_name ?? projectFallbackName(key) // FALLBACK-OK: explorer.md EXP-75 (a project filter that matches nothing is named from its path)
  const scopeLine = [
    periodTitle(url.period, today, summary.data ?? null), // FALLBACK-OK: explorer.md EXP-10 (an All time title waits for the data extent)
    ...filterTokens(url, 0, projectName).map((t) => t.label),
    ...(url.q.trim() === '' ? [] : [`"${url.q}"`]),
    ...(r ? [`${fmt.count(r.n_sessions)} ${r.n_sessions === 1 ? 'session' : 'sessions'}`] : []),
  ].join(' · ')

  // REV-22: a "Most in:" link opens that session in Review as blade 1, replacing this one; Back returns here.
  const openSession = (session: string) => nav.openBlade('session', { s: session, sview: 'review' })
  // REV-22: an evidence link opens its session at the row in Ledger (the SES-51 jump); the turn number is the display id's K.
  const openEvidence = (e: Evidence) => {
    const jump =
      e.row !== null && e.display_id !== null
        ? { t: Number(turnNumber(e.display_id)), step: e.row.type === 'turn' ? null : e.row.id, detail: true }
        : {}
    nav.openBlade('session', { s: e.session, sview: 'ledger', ...jump })
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 px-6 pb-3 pt-4">
        <h1 className="m-0 text-lead font-semibold text-ink1">Review</h1>
        <p className="m-0 mt-1 text-body text-ink3">{scopeLine}</p>
      </div>
      <ReviewView
        review={r}
        scope
        error={review.error}
        onRetry={review.retry}
        tz={tz}
        onOpenSession={openSession}
        onOpenEvidence={openEvidence}
      />
    </div>
  )
}
