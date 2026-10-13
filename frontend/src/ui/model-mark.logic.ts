import { modelFamily, type ModelFamily } from '../lib/model'

/** SYS-41: a 10 px shape per family, drawn on a 10 x 10 box. `char` names the shape for tests and tooltips. */
export const SHAPES: Record<ModelFamily, { char: string; d: string }> = {
  opus: { char: '◆', d: 'M5 0L10 5L5 10L0 5Z' },
  sonnet: { char: '●', d: 'M5 .5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9Z' },
  haiku: { char: '▲', d: 'M5 .5L9.8 9.5H.2Z' },
  fable: { char: '■', d: 'M.8 .8H9.2V9.2H.8Z' },
  gpt: { char: '⬡', d: 'M5 0L9.3 2.5V7.5L5 10L.7 7.5V2.5Z' },
  other: { char: '●', d: 'M5 2a3 3 0 1 0 0 6a3 3 0 1 0 0-6Z' },
}

export function markShape(modelId: string): { family: ModelFamily; char: string; d: string } {
  const family = modelFamily(modelId)
  return { family, ...SHAPES[family] }
}
