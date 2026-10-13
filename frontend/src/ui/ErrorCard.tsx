import { Icon } from './Icon'

/** SYS-56: a failed request. circle-alert is ink1 here, not error-red: this is a load failure, not a data fact.
 *  `unreachable` gives the FE-8 wording for a server that did not answer. */
export function ErrorCard({ noun, message, onRetry, unreachable = false }: { noun: string; message?: string; onRetry: () => void; unreachable?: boolean }) {
  return (
    <div role="alert" className="m-4 flex flex-col gap-2 rounded-card bg-l2 p-4">
      <div className="flex items-center gap-1.5">
        <Icon name="circle-alert" tone="inherit" className="text-ink1" />
        <span className="text-body font-medium text-ink1">{unreachable ? "Couldn't reach the Botsworth server" : `Couldn't load ${noun}`}</span>
      </div>
      {message ? <p className="break-words font-mono text-body text-ink2">{message}</p> : null}
      <div>
        <button type="button" onClick={onRetry} className="h-7 rounded-control border border-edge px-3 text-body text-ink1 hover:border-struct-line hover:text-struct">
          Retry
        </button>
      </div>
    </div>
  )
}
