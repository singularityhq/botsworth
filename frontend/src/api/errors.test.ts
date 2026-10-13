import { describe, expect, it } from 'vitest'
import { ApiError, NetError, classifyError, isAbort } from './errors'

describe('ApiError', () => {
  it('carries status, code and message, and is an Error', () => {
    const e = new ApiError(404, 'not_found', 'no such session')
    expect(e).toBeInstanceOf(Error)
    expect(e).toMatchObject({ status: 404, code: 'not_found', message: 'no such session', name: 'ApiError' })
  })
})

describe('classifyError (FE-8)', () => {
  it('maps each row of the FE-8 table', () => {
    expect(classifyError(new ApiError(503, 'no_store', 'x'))).toBe('no_store')
    expect(classifyError(new ApiError(503, 'schema_mismatch', 'x'))).toBe('schema_mismatch')
    expect(classifyError(new ApiError(404, 'not_found', 'x'))).toBe('failed')
    expect(classifyError(new ApiError(500, 'internal', 'x'))).toBe('failed')
    expect(classifyError(new NetError('x'))).toBe('unreachable')
    expect(classifyError(new DOMException('x', 'AbortError'))).toBe('aborted')
  })

  it('does not call a 404 with code no_store a missing store', () => {
    expect(classifyError(new ApiError(404, 'no_store', 'x'))).toBe('failed')
  })

  it('rejects anything else as a bug', () => {
    expect(() => classifyError(new Error('x'))).toThrow()
    expect(() => classifyError('x')).toThrow()
    expect(() => classifyError(null)).toThrow()
  })
})

describe('isAbort', () => {
  it('recognises an AbortError by name', () => {
    expect(isAbort(new DOMException('x', 'AbortError'))).toBe(true)
    expect(isAbort(new Error('x'))).toBe(false)
    expect(isAbort(null)).toBe(false)
  })
})
