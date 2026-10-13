import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { fmtBytes, IMAGE_BLOCKS, imageLine } from './detail.logic'
import { InfoLine, PointerLine, WellSkeleton } from './parts'
import { useSourceText, type SourceState, type SourceText } from './useSourceText'
import type { SourcePart } from '../../api/types'

/**
 * The states every lazy text shares (DET-60, DET-65, DET-66, DET-67). Loading shows three skeleton lines in the well; a missing source file is
 * one `info` line with the pointer; any other failure is the SYS-56 card for this section alone, with Retry; an image block is one line. A
 * ready text goes to `children`, followed by the "too large" line when the source is bigger than the first fetch.
 */
export function SourceBody({
  session, src, part, children, lines = 3,
}: { session: string; src: string; part: SourcePart; children: (d: SourceText) => React.ReactNode; lines?: number }) {
  const s = useSourceText(session, src, part)
  return (
    <SourceView state={s} src={src} lines={lines}>
      {children}
    </SourceView>
  )
}

/** The states of a lazy text drawn from a state already read (a section that needs the text for its own head reads it itself). */
export function SourceView({ state: s, src, children, lines = 3 }: { state: SourceState; src: string; children: (d: SourceText) => React.ReactNode; lines?: number }) {
  switch (s.status) {
    case 'loading':
      return <WellSkeleton lines={lines} />
    case 'missing':
      return (
        <div className="flex flex-col gap-1">
          <InfoLine>Text not available: source file not found</InfoLine>
          <PointerLine src={src} />
        </div>
      )
    case 'error':
      return (
        <div className="[&>div]:m-0">
          <ErrorCard noun="the text" message={s.message} onRetry={s.retry} />
        </div>
      )
    case 'ready': {
      const d = s.data
      if (d.block !== null && IMAGE_BLOCKS.has(d.block)) return <InfoLine>{imageLine(d.totalBytes)}</InfoLine>
      return (
        <div className="flex flex-col gap-2">
          {children(d)}
          {d.tooLarge ? <TooLarge src={src} totalBytes={d.totalBytes} /> : null}
        </div>
      )
    }
  }
}

/** DET-65: "Text too large to show in full: <size>" with the pointer and a copy button. */
export function TooLarge({ src, totalBytes }: { src: string; totalBytes: number }) {
  return (
    <div className="flex flex-col gap-1 text-ink3">
      <p className="flex items-center gap-1.5">
        <Icon name="info" tone="muted" />
        Text too large to show in full: {fmtBytes(totalBytes)}
      </p>
      <PointerLine src={src} />
    </div>
  )
}
