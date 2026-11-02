import { vi } from 'vitest'

// Tests run in one fixed browser zone, whatever the machine's (FE-9 [D50]). zone.test.ts loads the real module with vi.importActual.
vi.mock('./state/zone', () => ({
  browserZone: () => 'Asia/Kolkata',
  useBrowserZone: () => 'Asia/Kolkata',
}))

// Testing Library polls for 1 s by default. With the whole suite running in parallel on a loaded machine, a blade render
// in jsdom takes several seconds, so findBy/waitFor gave up before the render finished. Failing tests still fail; they
// only wait longer for a render that has not finished. Scoped to DOM tests (jsdom files); node-environment files skip it.
if (typeof document !== 'undefined') {
  const { configure } = await import('@testing-library/react')
  configure({ asyncUtilTimeout: 8000 })
}
