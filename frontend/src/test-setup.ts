import { vi } from 'vitest'

// Tests run in one fixed browser zone, whatever the machine's (FE-9 [D50]). zone.test.ts loads the real module with vi.importActual.
vi.mock('./state/zone', () => ({
  browserZone: () => 'Asia/Kolkata',
  useBrowserZone: () => 'Asia/Kolkata',
}))
