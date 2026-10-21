import { describe, expect, it } from 'vitest'
import { createNav } from './nav'
import type { UrlEnv } from './url'

interface Entry {
  how: 'push' | 'replace' | 'back'
  query: string
}
interface Slot {
  query: string
  state: unknown
}

/** A fake history: a stack of entries, a log of writes, and popstate-style listeners. */
function fakeEnv(initial = '', initialState: unknown = null): UrlEnv & { log: Entry[]; query: () => string; depth: () => number } {
  const stack: Slot[] = [{ query: initial, state: initialState }]
  let at = 0
  const log: Entry[] = []
  const listeners = new Set<() => void>()
  const cur = () => stack[at]
  return {
    log,
    query: () => cur().query,
    depth: () => stack.length,
    getSearch: () => (cur().query === '' ? '' : `?${cur().query}`),
    getState: () => cur().state,
    push(q, state) {
      stack.splice(at + 1)
      stack.push({ query: q, state: state ?? null })
      at += 1
      log.push({ how: 'push', query: q })
    },
    replace(q) {
      stack[at] = { query: q, state: cur().state }
      log.push({ how: 'replace', query: q })
    },
    back() {
      at -= 1
      log.push({ how: 'back', query: cur().query })
      listeners.forEach((l) => l()) // popstate arrives after back()
    },
    subscribe(l) {
      listeners.add(l)
      return () => listeners.delete(l)
    },
  }
}

describe('createNav', () => {
  it('replaces for a scope change (EXP-76)', () => {
    const env = fakeEnv()
    createNav(env).replace({ sort: 'wall' })
    expect(env.log).toEqual([{ how: 'replace', query: 'sort=wall' }])
  })

  it('merges a patch into the current state and keeps unrelated parameters', () => {
    const env = fakeEnv('period=yesterday&sort=wall')
    createNav(env).replace({ q: 'rbac' })
    expect(env.query()).toBe('period=yesterday&q=rbac&sort=wall')
  })

  it('writes defaults as absent', () => {
    const env = fakeEnv('sort=cost')
    createNav(env).replace({ sort: 'start' })
    expect(env.query()).toBe('')
  })

  it('makes no history write when the URL would not change', () => {
    const env = fakeEnv('sort=wall')
    const nav = createNav(env)
    nav.replace({ sort: 'wall' })
    nav.push({ sort: 'wall' })
    nav.openBlade('session', {})
    expect(env.log).toEqual([])
  })

  it('pushes when a blade opens (EXP-76, SES-10)', () => {
    const env = fakeEnv('sort=cost')
    createNav(env).openBlade('session', { s: 'a11ce5e5' })
    expect(env.log).toEqual([{ how: 'push', query: 'sort=cost&s=a11ce5e5' }])
  })

  it('opens the turn blade with a push and keeps the session blade state', () => {
    const env = fakeEnv('s=a&find=x&over=1')
    createNav(env).openBlade('turn', { t: 3 })
    expect(env.log).toEqual([{ how: 'push', query: 's=a&find=x&over=1&t=3' }])
  })

  it('opening a blade resets the blade and the ones deeper than it', () => {
    const env = fakeEnv('s=a&find=x&bars=time&t=3&step=y&zoom=1-2&detail=1')
    createNav(env).openBlade('session', { s: 'b' })
    expect(env.query()).toBe('s=b&bars=time') // bars is a preference and stays
  })

  it('opening a turn resets that turn\'s cursor and the detail blade, not the session', () => {
    const env = fakeEnv('s=a&find=x&t=3&step=y&zoom=1-2&detail=1')
    createNav(env).openBlade('turn', { t: 4 })
    expect(env.query()).toBe('s=a&find=x&t=4')
  })

  it('opening the detail blade keeps the turn cursor it opens on', () => {
    const env = fakeEnv('s=a&t=3&step=y')
    createNav(env).openBlade('detail', { detail: true })
    expect(env.query()).toBe('s=a&t=3&step=y&detail=1')
    expect(env.log[0].how).toBe('push')
  })

  it('moves the cursor and swaps the turn by replacing', () => {
    const env = fakeEnv('s=a&t=3&step=y')
    const nav = createNav(env)
    nav.replace({ step: 'z' })
    nav.replace({ t: 4, step: null })
    expect(env.log.map((e) => e.how)).toEqual(['replace', 'replace'])
    expect(env.query()).toBe('s=a&t=4')
  })

  describe('closeTo', () => {
    it('goes back one history step when this tab pushed the blade being closed', () => {
      const env = fakeEnv('s=a')
      const nav = createNav(env)
      nav.openBlade('turn', { t: 3 })
      nav.replace({ step: 'y' }) // moving the cursor keeps the entry's tag
      expect(env.depth()).toBe(2)
      nav.closeTo('session')
      expect(env.log.map((e) => e.how)).toEqual(['push', 'replace', 'back'])
      expect(env.query()).toBe('s=a')
      expect(env.depth()).toBe(2) // back moves; it does not add an entry
    })

    it('does nothing to the URL by itself: the new URL arrives through the popstate listeners', () => {
      const env = fakeEnv('s=a')
      let heard = 0
      env.subscribe(() => heard++)
      const nav = createNav(env)
      nav.openBlade('turn', { t: 3 })
      nav.closeTo('session')
      expect(heard).toBe(1)
    })

    it('rewrites the entry for a deep link: nothing in this tab pushed the blade', () => {
      const env = fakeEnv('s=a&t=3&step=y')
      createNav(env).closeTo('session')
      expect(env.log).toEqual([{ how: 'replace', query: 's=a' }])
    })

    it('rewrites the entry after a reload: the entry carries a tag from an earlier page load', () => {
      const env = fakeEnv('s=a&t=3', { opened: 'turn', tab: 'an-earlier-page-load' })
      createNav(env).closeTo('session')
      expect(env.log.map((e) => e.how)).toEqual(['replace'])
      expect(env.query()).toBe('s=a')
    })

    it('rewrites the entry when the closing spans more than the blade that was pushed', () => {
      const env = fakeEnv('s=a')
      const nav = createNav(env)
      nav.openBlade('turn', { t: 3 })
      nav.openBlade('detail', { detail: true })
      nav.closeTo('session') // closes turn and detail: one step cannot undo both
      expect(env.log.map((e) => e.how)).toEqual(['push', 'push', 'replace'])
      expect(env.query()).toBe('s=a')
    })

    it('goes back from detail to turn, then from turn to session, one step each', () => {
      const env = fakeEnv('s=a')
      const nav = createNav(env)
      nav.openBlade('turn', { t: 3 })
      nav.openBlade('detail', { detail: true })
      nav.closeTo('turn')
      expect(env.query()).toBe('s=a&t=3')
      nav.closeTo('session')
      expect(env.query()).toBe('s=a')
      expect(env.log.filter((e) => e.how === 'back')).toHaveLength(2)
    })

    it('rewrites the entry when the top blade was opened by a plain push', () => {
      const env = fakeEnv('s=a')
      const nav = createNav(env)
      nav.push({ t: 3 })
      nav.closeTo('session')
      expect(env.log.map((e) => e.how)).toEqual(['push', 'replace'])
    })

    it('rewrites the entry when the session blade came from a deep link and Esc closes it to the explorer', () => {
      const env = fakeEnv('s=a&bars=tokens')
      createNav(env).closeTo('explorer')
      expect(env.query()).toBe('bars=tokens')
      expect(env.log.map((e) => e.how)).toEqual(['replace'])
    })
  })

  it('closing to a level that is already the top changes nothing', () => {
    const env = fakeEnv('s=a')
    createNav(env).closeTo('session')
    expect(env.log).toEqual([])
  })
})

describe('scope review blade (REV-21)', () => {
  it('opens blade 1 with review=1 and a history entry, keeping the scope', () => {
    const env = fakeEnv('period=today&q=rbac')
    createNav(env).openBlade('review', {})
    expect(env.log).toEqual([{ how: 'push', query: 'period=today&q=rbac&review=1' }])
  })

  it('replaces an open session blade and closes the blades to its right', () => {
    const env = fakeEnv('s=a&t=3&detail=1&sview=waterfall')
    createNav(env).openBlade('review', {})
    expect(env.query()).toBe('review=1')
  })

  it('opening a session from the review drops review=1, and Back returns to the review', () => {
    const env = fakeEnv('')
    const nav = createNav(env)
    nav.openBlade('review', {})
    nav.openBlade('session', { s: 'a', sview: 'review' })
    expect(env.query()).toBe('s=a&sview=review')
    env.back() // browser Back
    expect(env.query()).toBe('review=1')
  })

  it('closes every blade when the explorer is the target after a session was opened from the review (the "Sessions" crumb, Esc)', () => {
    const env = fakeEnv('')
    const nav = createNav(env)
    nav.openBlade('review', {})
    nav.openBlade('session', { s: 'a', sview: 'review' })
    nav.closeTo('explorer')
    expect(env.query()).toBe('')
    expect(env.log.at(-1)).toEqual({ how: 'replace', query: '' })
  })

  it('still goes back one step when a session opened from the explorer closes', () => {
    const env = fakeEnv('')
    const nav = createNav(env)
    nav.openBlade('session', { s: 'a' })
    nav.closeTo('explorer')
    expect(env.log.at(-1)!.how).toBe('back')
  })

  it('closes the review by going back when this tab opened it', () => {
    const env = fakeEnv('')
    const nav = createNav(env)
    nav.openBlade('review', {})
    nav.closeTo('explorer')
    expect(env.log.map((e) => e.how)).toEqual(['push', 'back'])
    expect(env.query()).toBe('')
  })

  it('closes a review from a deep link by rewriting the entry', () => {
    const env = fakeEnv('review=1')
    createNav(env).closeTo('explorer')
    expect(env.query()).toBe('')
  })
})
