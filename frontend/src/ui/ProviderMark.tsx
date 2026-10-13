import type { Provider } from '../api/types'
import { PROVIDER_NAME } from './provider'

// SYS-24: three marks on the 24 grid, 1.5 stroke, round caps and joins, line only.
const GLYPHS: Record<Provider, React.ReactNode> = {
  // 6-ray asterisk
  claude_code: (
    <>
      <path d="M12 3v18" />
      <path d="M4.2 7.5l15.6 9" />
      <path d="M4.2 16.5l15.6-9" />
    </>
  ),
  // hexagonal knot of 3 interlocked strokes
  codex: (
    <>
      <path d="M12 3.5l6.5 8.5l-6.5 8.5l-6.5-8.5z" />
      <path d="M12 3.5l6.5 8.5l-6.5 8.5l-6.5-8.5z" transform="rotate(60 12 12)" />
      <path d="M12 3.5l6.5 8.5l-6.5 8.5l-6.5-8.5z" transform="rotate(120 12 12)" />
    </>
  ),
  // pi inside a 20 px circle
  omp: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M7.5 9h9" />
      <path d="M10 9v6.5" />
      <path d="M14 9v5.2q0 1.8 1.8 1.8" />
    </>
  ),
}

export function ProviderMark({ provider, size = 16, label = true, className = '' }: { provider: Provider; size?: 16 | 20; label?: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-label={label ? PROVIDER_NAME[provider] : undefined}
      aria-hidden={label ? undefined : true}
      className={`shrink-0 text-ink3 ${className}`}
    >
      {GLYPHS[provider]}
    </svg>
  )
}
