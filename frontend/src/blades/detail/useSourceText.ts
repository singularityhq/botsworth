import { useEffect, useState } from 'react'
import { client } from '../../api/client'
import { ApiError, NetError } from '../../api/errors'
import type { SourcePart } from '../../api/types'
import { ResponseCache } from '../../api/useApi'
import { utf8Bytes } from './detail.logic'

/**
 * DET-60, DET-65, DET-66: lazy full text through `GET /api/source`. One fetch per (src, part) while a session stays open: the text of a row
 * the user steps away from and back to is not fetched again. The first fetch asks for at most 256 KB; an output larger than that also gets a
 * second fetch of its last 16 KB, so its last lines are real (API-26).
 */

export const FIRST_FETCH_BYTES = 262_144
export const TAIL_FETCH_BYTES = 16_384
const CACHE_ENTRIES = 80

export interface SourceText {
  src: string
  part: SourcePart
  text: string
  /** The whole source's size in bytes. */
  totalBytes: number
  /** Bytes the first fetch brought. */
  fetchedBytes: number
  /** The source is larger than the first fetch (DET-65). */
  tooLarge: boolean
  /** The end of an output that was too large to fetch whole; null otherwise. */
  tail: string | null
  /** The content block type at the pointer (API-26): `thinking`, `redacted_thinking`, `reasoning`, `text`, `tool_result`, … */
  block: string | null
}

export type SourceState =
  | { status: 'loading' }
  | { status: 'ready'; data: SourceText }
  | { status: 'missing' }
  | { status: 'error'; message: string; retry: () => void }

/** Fetch a source's text. Throws what the client throws; an abort rejects with AbortError. */
export async function fetchSourceText(src: string, part: SourcePart, signal?: AbortSignal): Promise<SourceText> {
  const first = await client.source(src, { part, offset: 0, limit: FIRST_FETCH_BYTES }, signal)
  const fetchedBytes = utf8Bytes(first.text)
  const tooLarge = first.total_bytes > fetchedBytes
  let tail: string | null = null
  if (tooLarge && part === 'output') {
    const t = await client.source(src, { part, offset: first.total_bytes - TAIL_FETCH_BYTES, limit: TAIL_FETCH_BYTES }, signal)
    tail = t.text
  }
  return { src, part, text: first.text, totalBytes: first.total_bytes, fetchedBytes, tooLarge, tail, block: first.block }
}

/** The texts fetched in this session. Emptied when the session changes. */
const cache = new ResponseCache(CACHE_ENTRIES)
let cacheSession: string | null = null
const keyOf = (src: string, part: SourcePart): string => `${part} ${src}`

/** Forget every fetched text (tests, and a session change). */
export function clearSourceCache(): void {
  cache.clear()
  cacheSession = null
}

function enter(session: string): void {
  if (cacheSession !== session) {
    cache.clear()
    cacheSession = session
  }
}

/** A cached text, or undefined. */
export function cachedSource(session: string, src: string, part: SourcePart): SourceText | undefined {
  enter(session)
  return cache.get<SourceText>(keyOf(src, part))
}

interface Settled {
  key: string
  state: Exclude<SourceState, { status: 'loading' }>
}

/**
 * The text at `src` for `part`; `src` null (the row has no pointer) fetches nothing. Changing the pointer or leaving aborts the request
 * (DET-4: the row changed). A 404 `source_missing` is its own state (DET-66); any other failure is an error with Retry.
 */
export function useSourceText(session: string, src: string | null, part: SourcePart): SourceState {
  const [settled, setSettled] = useState<Settled | null>(null)
  const [attempt, setAttempt] = useState(0)
  const key = src === null ? null : `${session} ${keyOf(src, part)}`
  const cached = src === null ? undefined : cachedSource(session, src, part)

  useEffect(() => {
    if (src === null || key === null || cache.has(keyOf(src, part))) return
    let live = true
    const { promise, release } = cache.load<SourceText>(keyOf(src, part), (signal) => fetchSourceText(src, part, signal))
    promise.then(
      (data) => live && setSettled({ key, state: { status: 'ready', data } }),
      (e: unknown) => {
        if (!live || (e as { name?: string }).name === 'AbortError') return
        if (e instanceof ApiError && e.code === 'source_missing') {
          setSettled({ key, state: { status: 'missing' } })
          return
        }
        if (e instanceof ApiError || e instanceof NetError) {
          const retry = () => {
            setSettled(null)
            setAttempt((n) => n + 1)
          }
          setSettled({ key, state: { status: 'error', message: e.message, retry } })
          return
        }
        setSettled(() => {
          throw e // a bug in the fetcher: the nearest error boundary shows it
        })
      },
    )
    return () => {
      live = false
      release()
    }
  }, [session, src, part, key, attempt])

  if (cached !== undefined) return { status: 'ready', data: cached }
  if (settled !== null && settled.key === key) return settled.state
  return { status: 'loading' }
}
