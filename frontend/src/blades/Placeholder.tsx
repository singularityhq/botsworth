import { DEFAULT_URL_STATE, useUrlState } from '../state/url'

/** F1b placeholder body: the blade title plus the URL parameters that are set. F2 to F4 replace each blade that uses it. */
export function Placeholder({ title, width, compact }: { title: string; width: number; compact: boolean }) {
  const url = useUrlState()
  const set = Object.entries(url).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(DEFAULT_URL_STATE[k as keyof typeof DEFAULT_URL_STATE]))
  return (
    <div className="flex h-full flex-col gap-3 p-6">
      <h2 className="text-lead font-semibold text-ink1">{title}</h2>
      <p className="text-body text-ink3">
        {width} px{compact ? ', compact form' : ''}
      </p>
      {set.length === 0 ? (
        <p className="text-body text-ink3">No URL parameters.</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-body">
          {set.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-ink3">{k}</dt>
              <dd className="font-mono text-ink2">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
