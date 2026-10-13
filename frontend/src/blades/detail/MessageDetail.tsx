import { Section } from './parts'
import { charCount, fmtBytes } from './detail.logic'
import { SourceView } from './SourceBody'
import { TextWell } from './TextWell'
import { useSourceText } from './useSourceText'

export interface MessageDetailProps {
  session: string
  textSrc: string
  /** A slash command's name and arguments (the turn's label); shown in a mono well above the message (DET-50). */
  slash: string | null
}

/** DET-50: the turn's opening message in full: not clamped, 76 ch measure, the character count in the head, a copy button. */
export function MessageDetailView({ session, textSrc, slash }: MessageDetailProps) {
  const state = useSourceText(session, textSrc, 'text')
  const count =
    state.status !== 'ready' ? null : state.data.tooLarge ? fmtBytes(state.data.totalBytes) : `${charCount(state.data.text).toLocaleString('en-US')} characters`
  return (
    <div className="flex flex-col gap-8">
      {slash ? (
        <Section icon="square-slash" title="Command">
          <TextWell text={slash} variant="code" label="slash command" />
        </Section>
      ) : null}
      <Section icon="message-square-text" title="Message" aside={count ? <span className="fig">{count}</span> : null}>
        <SourceView state={state} src={textSrc} lines={4}>
          {(d) => <TextWell text={d.text} variant="prose" label="message" />}
        </SourceView>
      </Section>
    </div>
  )
}
