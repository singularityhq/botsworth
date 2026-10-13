import { useState } from 'react'
import type { ToolDetail as ToolDetailWire, ToolRow } from '../../api/types'
import { fmt } from '../../lib/format'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { Tooltip } from '../../ui/Tooltip'
import { DERIVED_TIP, toolName } from '../turn/rowName'
import type { LoadError } from './CallDetail'
import { countLines, fmtBytes, isShell, prettyJson, SILENCE_NOTE_MS, toolStatus } from './detail.logic'
import { Disclosure, Hero, HeroFigure, InfoLine, Section, WellSkeleton } from './parts'
import { OutputWell } from './OutputWell'
import { SourceBody } from './SourceBody'
import { useSourceText } from './useSourceText'
import { TextWell } from './TextWell'

type ToolData = Omit<ToolRow, 'type'>

export interface ToolDetailProps {
  session: string
  row: ToolRow | undefined
  detail: ToolDetailWire | undefined
  error: LoadError | null
}

/** DET-20 to DET-24: a tool or command. */
export function ToolDetailView({ session, row, detail, error }: ToolDetailProps) {
  const t: ToolData | undefined = detail ?? row
  if (t === undefined) return null
  const status = toolStatus(t)
  const derived = t.time_prov === 'derived'
  const dur = t.dur_ms === null ? '—' : `${derived ? '≈' : ''}${fmt.dur(t.dur_ms)}`
  const name = toolName({ ...t, type: 'tool' })
  const shell = isShell(t)
  const waits = t.waits_on_user
  // DET-69: with no pointer for any section, one line says so.
  const target = t.file_path ?? t.target // FALLBACK-OK: DET-23 (the main target is the file path, else the pattern or URL)
  const noText = detail !== undefined && !t.command_src && !detail.output_src && !detail.input_src && (shell ? t.command === null : target === '') && t.result_bytes !== 0
  const silent = t.max_silence_ms !== null && t.max_silence_ms >= SILENCE_NOTE_MS
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <Hero>
          {derived ? (
            <Tooltip content={DERIVED_TIP}>
              <HeroFigure>{dur}</HeroFigure>
            </Tooltip>
          ) : (
            <HeroFigure>{dur}</HeroFigure>
          )}
          {status ? (
            status.failed ? (
              <span className="inline-flex items-center gap-1 text-error">
                <Icon name="circle-alert" tone="error" />
                {status.text}
              </span>
            ) : (
              <span className="text-ink3">{status.text}</span>
            )
          ) : null}
          {t.tool_name ? <span className="text-ink3">{t.tool_name}</span> : null}
          {t.result_bytes !== null && t.result_bytes > 0 ? <span className="text-ink3">{fmtBytes(t.result_bytes)} output</span> : null}
        </Hero>
        {silent ? <InfoLine>No records for {fmt.dur(t.max_silence_ms)} inside this call. Its duration includes that time.</InfoLine> : null}
        {waits ? <InfoLine>This tool waits for your answer. Its duration includes your reply.</InfoLine> : null}
        {t.skill_load !== null ? (
          <p className="flex items-center gap-1.5 text-ink2">
            <Icon name="book-open" tone="quiet" />
            <span>
              <b className="font-semibold text-ink1">{t.skill_load}</b> loaded here.
            </span>
          </p>
        ) : null}
      </div>
      {noText ? <InfoLine>No text recorded for this tool call.</InfoLine> : null}
      {shell ? <CommandSection session={session} t={t} /> : <InputSection session={session} t={t} detail={detail} glyph={name.glyph} />}
      {error ? <ErrorCard noun="details" message={error.message} onRetry={error.retry} /> : <OutputSection session={session} t={t} detail={detail} />}
    </div>
  )
}

/** DET-23: the full command from the source, in a code well clamped to 6 lines ("All N lines"), with copy. */
function CommandSection({ session, t }: { session: string; t: ToolData }) {
  const stored = t.command
  // DET-23: the shown command is the full one from the source, not the stored preview.
  const src = t.command_src
  if (src === null && stored === null) return null // DET-69: no pointer and nothing stored: the section is not rendered
  return (
    <Section icon="terminal" title="Command">
      {src === null ? (
        <TextWell text={stored ?? ''} variant="code" clamp={6} expander={(n) => `All ${n} lines`} label="command" />
      ) : (
        <SourceBody session={session} src={src} part="input">
          {(d) => <TextWell text={d.text} variant="code" clamp={6} expander={(n) => `All ${n} lines`} label="command" />}
        </SourceBody>
      )}
    </Section>
  )
}

/** DET-23: the main target (path, pattern, URL) in the same well, and a collapsed "Raw input" with the call's JSON. */
function InputSection({ session, t, detail, glyph }: { session: string; t: ToolData; detail: ToolDetailWire | undefined; glyph: ReturnType<typeof toolName>['glyph'] }) {
  const [raw, setRaw] = useState(false)
  const target = t.file_path ?? t.target // FALLBACK-OK: DET-23 (the main target is the file path, else the pattern or URL)
  if (target === '' && !detail?.input_src) return null // DET-69: nothing to show
  return (
    <Section icon={glyph} title="Input">
      {target !== '' ? <TextWell text={target} variant="code" clamp={6} expander={(n) => `All ${n} lines`} label="input" /> : null}
      {detail?.input_src || detail === undefined ? (
        <div className="flex flex-col gap-2">
          <div>
            <Disclosure open={raw} onClick={() => setRaw((o) => !o)}>
              Raw input
            </Disclosure>
          </div>
          {raw ? detail?.input_src ? <RawInput session={session} src={detail.input_src} /> : <WellSkeleton /> : null}
        </div>
      ) : null}
    </Section>
  )
}

/** The input JSON, pretty-printed when it parses. */
function RawInput({ session, src }: { session: string; src: string }) {
  return (
    <SourceBody session={session} src={src} part="input">
      {(d) => <TextWell text={prettyJson(d.text)} variant="code" clamp={12} expander={(n) => `All ${n} lines`} label="raw input" />}
    </SourceBody>
  )
}

/** DET-24, DET-65: "Output" with its size and line count, the head, the divider and the tail. */
function OutputSection({ session, t, detail }: { session: string; t: ToolData; detail: ToolDetailWire | undefined }) {
  if (t.result_bytes === 0) {
    return (
      <Section title="Output">
        <OutputWell text="" tail={null} />
      </Section>
    )
  }
  const src = detail?.output_src
  if (detail !== undefined && !src) return null // DET-69: the call has no output source
  const size = t.result_bytes !== null && t.result_bytes > 0 ? fmtBytes(t.result_bytes) : null
  return (
    <Section title="Output" aside={<OutputMeta size={size} session={session} src={src} />}>
      {src === undefined ? (
        <WellSkeleton />
      ) : (
        <SourceBody session={session} src={src} part="output">
          {(d) => <OutputWell text={d.text} tail={d.tail} />}
        </SourceBody>
      )}
    </Section>
  )
}

/** "11 KB · 214 lines": the size is known from the store, the line count once the whole text is in. */
function OutputMeta({ size, session, src }: { size: string | null; session: string; src: string | undefined }) {
  const s = useSourceText(session, src ?? null, 'output')
  const lines = s.status === 'ready' && !s.data.tooLarge ? `${countLines(s.data.text)} lines` : null
  const text = [size, lines].filter((x): x is string => x !== null).join(' · ')
  return text === '' ? null : <span>{text}</span>
}
