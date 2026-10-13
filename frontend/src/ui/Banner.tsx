import { useEffect, useState } from 'react'
import { BANNER_TEXT, type BannerKind } from './banner.logic'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'

const CMD = 'botsworth ingest'

function CommandChip() {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const t = setTimeout(() => setCopied(false), 1200)
    return () => clearTimeout(t)
  }, [copied])
  return (
    <span className="inline-flex items-center gap-1 rounded-chip bg-l3 py-0.5 pr-0.5 pl-2 font-mono text-body text-ink1">
      {CMD}
      <Tooltip content={copied ? 'Copied' : 'Copy'}>
        <button
          type="button"
          aria-label="Copy command"
          onClick={() => navigator.clipboard.writeText(CMD).then(() => setCopied(true))}
          className="inline-flex size-7 items-center justify-center rounded-chip hover:bg-l4"
        >
          <Icon name={copied ? 'check' : 'copy'} tone="muted" />
        </button>
      </Tooltip>
    </span>
  )
}

/** SYS-57 / SYS-57a: a full-width neutral banner under the top bar. l2, database glyph, no colour from the other axes. */
export function Banner({ kind }: { kind: BannerKind }) {
  const withCommand = kind !== 'ingest'
  return (
    <div role="status" className="flex shrink-0 items-center gap-2 border-b border-rule bg-l2 px-5 py-1.5 text-body">
      <span className="inline-flex items-center gap-1.5">
        <Icon name="database" tone="quiet" />
        <span className="text-ink1">{BANNER_TEXT[kind]}</span>
      </span>
      {withCommand ? (
        <>
          <span className="text-ink2">Run</span>
          <CommandChip />
          <span className="text-ink2">then reload.</span>
        </>
      ) : null}
    </div>
  )
}
