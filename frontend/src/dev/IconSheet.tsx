import { Icon } from '../ui/Icon'
import { GLYPH, ICON_NAMES } from '../ui/glyphs'
import { ProviderMark } from '../ui/ProviderMark'
import { ProjectDot } from '../ui/ProjectDot'
import { ModelMark } from '../ui/ModelMark'

// Development page (`?dev=icons`, DEV builds only): every SYS-28 glyph with its concept names.
const CONCEPTS = Object.entries(GLYPH).reduce<Record<string, string[]>>((acc, [concept, name]) => {
  ;(acc[name] ??= []).push(concept)
  return acc
}, {})

export default function IconSheet() {
  return (
    <main className="h-full overflow-auto p-6">
      <h1 className="mb-4 text-lead font-semibold text-ink1">Icon sheet</h1>
      <h2 className="mb-2 text-body font-semibold text-ink1">Provider marks and dots</h2>
      <div className="mb-6 flex items-center gap-6 text-body text-ink2">
        <span className="flex items-center gap-2"><ProviderMark provider="claude_code" size={20} />Claude Code</span>
        <span className="flex items-center gap-2"><ProviderMark provider="codex" size={20} />Codex</span>
        <span className="flex items-center gap-2"><ProviderMark provider="omp" size={20} />omp</span>
        {([1, 2, 3, 4, 5, 6, null] as const).map((s) => (
          <span key={String(s)} className="flex items-center gap-2"><ProjectDot slot={s} size={10} />{s ?? 'other'}</span>
        ))}
      </div>
      <h2 className="mb-2 text-body font-semibold text-ink1">Model marks</h2>
      <div className="mb-6 flex items-center gap-6">
        {['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1', 'gpt-5.4', 'deepseek-v4'].map((m) => (
          <ModelMark key={m} model={m} />
        ))}
      </div>
      <h2 className="mb-2 text-body font-semibold text-ink1">Lucide glyphs ({ICON_NAMES.length})</h2>
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-6 gap-y-3">
        {ICON_NAMES.map((n) => (
          <li key={n} className="flex items-start gap-3 text-body">
            <Icon name={n} size={20} tone="quiet" />
            <span className="min-w-0">
              <span className="block font-mono text-ink1">{n}</span>
              <span className="block text-ink3">{CONCEPTS[n].join(', ')}</span>
            </span>
          </li>
        ))}
      </ul>
    </main>
  )
}
