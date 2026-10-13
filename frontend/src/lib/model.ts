/**
 * Model family and short name (SYS-41). The family decides the mark's shape;
 * anything that is not opus, sonnet, haiku, fable or gpt is "other".
 */

export type ModelFamily = 'opus' | 'sonnet' | 'haiku' | 'fable' | 'gpt' | 'other'

const CLAUDE_RE = /(opus|sonnet|haiku|fable)-(\d+)(?:-(\d)(?!\d))?/

/** `claude-opus-5-5-20251101` → { family: 'opus', short: 'opus 5.5' }. */
function parse(id: string): { family: ModelFamily; short: string } {
  const m = CLAUDE_RE.exec(id)
  if (m) return { family: m[1] as ModelFamily, short: `${m[1]} ${m[2]}${m[3] ? `.${m[3]}` : ''}` }
  if (/^gpt/.test(id)) return { family: 'gpt', short: id.replace(/^gpt-/, 'gpt ') }
  return { family: 'other', short: id }
}

// FALLBACK-OK: system.md SYS-41 ("anything else is 'other'"; CallRow.model is null when the call recorded no model)
const UNKNOWN = 'unknown'

export function modelFamily(id: string | null): ModelFamily {
  return id ? parse(id).family : 'other'
}

export function modelShort(id: string | null): string {
  return id ? parse(id).short : UNKNOWN
}
