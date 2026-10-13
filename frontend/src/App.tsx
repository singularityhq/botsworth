import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PaletteHost } from './blades/explorer/palette'
import { DetailBlade } from './blades/detail'
import { ExplorerBlade } from './blades/explorer'
import { SessionBlade } from './blades/session'
import { TurnBlade } from './blades/turn'
import { useNav } from './state/nav'
import { useUrlState } from './state/url'
import { BladeStrip, type BladeSpec } from './shell/BladeStrip'
import { bladeKeys, focusAfterChange, type BladeKey } from './shell/blades'
import { CrumbProvider } from './shell/CrumbProvider'
import { StatusHost, StatusProvider } from './blades/shared/status'
import { useGlobalKeys } from './shell/keys'
import { moveFocus } from './shell/strip.logic'
import { TopBar } from './shell/TopBar'
import { useStoreStatus } from './shell/useStoreStatus'
import { Banner } from './ui/Banner'
import { lazy, Suspense } from 'react'

const BLADES: Record<BladeKey, { label: string; Cmp: (p: { width: number; compact: boolean }) => React.ReactNode }> = {
  explorer: { label: 'Sessions', Cmp: ExplorerBlade },
  session: { label: 'Session', Cmp: SessionBlade },
  turn: { label: 'Turn', Cmp: TurnBlade },
  detail: { label: 'Detail', Cmp: DetailBlade },
}

// The icon sheet and the pattern gallery are development pages: `import.meta.env.DEV` is false in a product build, so the branch and the module are dropped.
const DEV_PAGES = import.meta.env.DEV
  ? { icons: lazy(() => import('./dev/IconSheet')), patterns: lazy(() => import('./dev/Patterns')) }
  : null

function Shell() {
  const url = useUrlState()
  const nav = useNav()
  const { status, error, banner } = useStoreStatus()
  const keys = bladeKeys(url)
  const keysJoined = keys.join('|')
  const [focusKey, setFocusKey] = useState<BladeKey>(keys[keys.length - 1])
  const prevKeys = useRef(keys)
  const [paletteOpen, setPaletteOpen] = useState(false)

  useEffect(() => {
    const prev = prevKeys.current
    setFocusKey((cur) => focusAfterChange(prev, keys, cur) as BladeKey)
    prevKeys.current = keys
    // keysJoined is the dependency that matters; `keys` is rebuilt on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keysJoined])

  const blades: BladeSpec[] = useMemo(
    () =>
      keysJoined.split('|').map((k) => {
        const { label, Cmp } = BLADES[k as BladeKey]
        return { key: k, label, render: (width: number, compact: boolean) => <Cmp width={width} compact={compact} /> }
      }),
    [keysJoined],
  )

  // Back closes the rightmost blade because opening pushed a history entry (EXP-76); Esc and crumbs use replace.
  const closeTo = useCallback((index: number) => nav.closeTo(keysJoined.split('|')[index] as BladeKey), [nav, keysJoined])
  useGlobalKeys({
    bladeKeys: keys,
    focusKey,
    setFocusIndex: (delta) => setFocusKey(keys[moveFocus(keys.length, keys.indexOf(focusKey), delta)]),
    closeRightmost: () => closeTo(keys.length - 2),
    togglePalette: () => setPaletteOpen((o) => !o),
  })

  return (
    <div className="flex h-full flex-col">
      <TopBar blades={blades} onCloseTo={closeTo} onPalette={() => setPaletteOpen((o) => !o)} status={status} error={error} />
      {banner ? <Banner kind={banner} /> : null}
      <BladeStrip blades={blades} focusKey={focusKey} onFocus={(k) => setFocusKey(k as BladeKey)} />
      <StatusHost sessionOpen={keys.includes('session')} />
      <PaletteHost open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  )
}

export default function App() {
  const dev = new URLSearchParams(window.location.search).get('dev')
  const Dev = DEV_PAGES && (dev === 'icons' || dev === 'patterns') ? DEV_PAGES[dev] : null
  if (Dev) {
    return (
      <Suspense fallback={null}>
        <Dev />
      </Suspense>
    )
  }
  return (
    <CrumbProvider>
      <StatusProvider>
        <Shell />
      </StatusProvider>
    </CrumbProvider>
  )
}
