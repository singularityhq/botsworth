/**
 * Hand-written bodies, one per endpoint, checked against the wire types by
 * `tsc` (FE-1, TST-6). A renamed, missing or mistyped field fails the build.
 * Only test files import this module; the product never does (FE-3).
 *
 * JSON imports widen string literals to `string`, so the check compares
 * against `Widen<Wire>`: every field name, nesting, nullability and
 * number/boolean/string kind is checked; the members of a string union are not.
 */
import type {
  AgentDetail, AgentRowPage, CallDetail, DaySeries, Facets, FindResult, Histogram, IngestStatus, MessageDetail,
  PaletteResult, PeriodSummary, Resolved, SessionDetail, SessionPage, SourceResponse, ToolDetail, TurnList, TurnRows,
} from '../types'
import agentRows from './agent-rows.json' with { type: 'json' }
import detailAgent from './detail-agent.json' with { type: 'json' }
import detailCall from './detail-call.json' with { type: 'json' }
import detailMessage from './detail-message.json' with { type: 'json' }
import detailTool from './detail-tool.json' with { type: 'json' }
import explorerDays from './explorer-days.json' with { type: 'json' }
import explorerFacets from './explorer-facets.json' with { type: 'json' }
import explorerSessions from './explorer-sessions.json' with { type: 'json' }
import explorerSessionsGrouped from './explorer-sessions-grouped.json' with { type: 'json' }
import explorerSummary from './explorer-summary.json' with { type: 'json' }
import ingestStatus from './ingest-status.json' with { type: 'json' }
import palette from './palette.json' with { type: 'json' }
import sessionDetail from './session-detail.json' with { type: 'json' }
import sessionFind from './session-find.json' with { type: 'json' }
import sessionHistogram from './session-histogram.json' with { type: 'json' }
import sessionResolve from './session-resolve.json' with { type: 'json' }
import sessionTurns from './session-turns.json' with { type: 'json' }
import source from './source.json' with { type: 'json' }
import turnHistogram from './turn-histogram.json' with { type: 'json' }
import turnRows from './turn-rows.json' with { type: 'json' }

export type Widen<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends readonly (infer U)[]
    ? Widen<U>[]
    : T extends object
      ? { [K in keyof T]: Widen<T[K]> }
      : T

export const fixtures = {
  ingestStatus: ingestStatus satisfies Widen<IngestStatus>,
  explorerSummary: explorerSummary satisfies Widen<PeriodSummary>,
  explorerDays: explorerDays satisfies Widen<DaySeries>,
  explorerSessions: explorerSessions satisfies Widen<SessionPage>,
  explorerSessionsGrouped: explorerSessionsGrouped satisfies Widen<SessionPage>,
  explorerFacets: explorerFacets satisfies Widen<Facets>,
  palette: palette satisfies Widen<PaletteResult>,
  sessionDetail: sessionDetail satisfies Widen<SessionDetail>,
  sessionTurns: sessionTurns satisfies Widen<TurnList>,
  sessionFind: sessionFind satisfies Widen<FindResult>,
  sessionHistogram: sessionHistogram satisfies Widen<Histogram>,
  sessionResolve: sessionResolve satisfies Widen<Resolved>,
  turnHistogram: turnHistogram satisfies Widen<Histogram>,
  turnRows: turnRows satisfies Widen<TurnRows>,
  agentRows: agentRows satisfies Widen<AgentRowPage>,
  detailCall: detailCall satisfies Widen<CallDetail>,
  detailTool: detailTool satisfies Widen<ToolDetail>,
  detailAgent: detailAgent satisfies Widen<AgentDetail>,
  detailMessage: detailMessage satisfies Widen<MessageDetail>,
  source: source satisfies Widen<SourceResponse>,
}
